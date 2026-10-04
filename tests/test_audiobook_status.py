from __future__ import annotations

import json
from collections import Counter

from multiscope_support import make_audiobook_project
from project_support import Adapter

from readio.api import AudiobookExportOptions, Readio
from readio.api.types import ProjectSettings
from readio.engines.registry import _registry
from readio.project import hash_file, load_project
from readio.stages import audiobook_export as audiobook_export_stage
from readio.stages.export import store_export_state, target_record
from readio.stages.pipeline import project_status, render_project
from readio.stages.planning import plan_project


def test_status_reports_provenance_scope_and_aggregate_cache_freshness(
    tmp_path, monkeypatch
) -> None:
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    project, cfg, _ = make_audiobook_project(tmp_path)
    original_manifest = project.paths["source"].read_bytes()
    plan_project(project, cfg)
    render_project(project, cfg)

    status = project_status(project)
    stages = {row["stage"]: row for row in status["stages"]}
    assert all(
        stages[name]["state"] == "current"
        for name in ("plan", "synthesis", "composition", "output")
    )
    assert stages["synthesis"]["scopes"] == len(project.document_scopes())
    assert stages["synthesis"]["required"] == stages["synthesis"]["total"]

    trace = json.loads(project.paths["synthesis_trace"].read_text(encoding="utf-8"))
    counts = Counter(item["synthesis_key"] for item in trace["segments"])
    missing = next(item for item in trace["segments"] if counts[item["synthesis_key"]] == 1)
    project.state_root.joinpath(missing["cache_path"]).unlink()
    stale = {row["stage"]: row for row in project_status(project)["stages"]}
    assert stale["synthesis"]["state"] == "stale"
    assert stale["synthesis"]["missing"] == 1
    assert stale["composition"]["blocked_by"] == "synthesis"
    assert stale["output"]["blocked_by"] == "composition"

    changed_manifest = json.loads(original_manifest)
    changed_manifest["metadata"]["title"] = "Externally edited title"
    changed_manifest_bytes = json.dumps(changed_manifest, sort_keys=True).encode("utf-8")
    project.paths["source"].write_bytes(changed_manifest_bytes)
    provenance = {row["stage"]: row for row in project_status(project)["stages"]}
    assert provenance["source"]["reason"] == "source.stale.hash_changed"
    assert project.paths["source"].read_bytes() == changed_manifest_bytes
    assert "reinitialize" in next(
        issue["message"]
        for issue in project_status(project)["issues"]
        if issue["stage"] == "source"
    )

    project.paths["source"].write_bytes(original_manifest)
    changed_scope = project.document_scopes()[2]
    chapter_path = project.workspace_path(changed_scope.path)
    chapter_path.write_text(
        chapter_path.read_text(encoding="utf-8") + "\nChanged.", encoding="utf-8"
    )
    changed = {row["stage"]: row for row in project_status(project)["stages"]}
    assert changed["plan"]["reason"] == "plan.stale.document_changed"
    assert changed["plan"]["scope_id"] == changed_scope.id
    assert changed["synthesis"]["blocked_by"] == "plan"


def test_status_reports_audiobook_desired_output_settings_staleness(tmp_path, monkeypatch) -> None:
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    project, cfg, _ = make_audiobook_project(tmp_path)
    plan_project(project, cfg)
    render_project(project, cfg)
    app = Readio(cfg)
    old_options = AudiobookExportOptions(
        output=project.root / "exports" / "old.m4b",
        title="Old title",
        bitrate="128k",
    )
    app.projects.configure(project.root, ProjectSettings(audiobook_export=old_options))
    project = load_project(project.root)
    prepared = audiobook_export_stage.prepare_audiobook_export(
        project, title=old_options.title, bitrate=old_options.bitrate
    )
    old_options.output.parent.mkdir(parents=True, exist_ok=True)
    old_options.output.write_bytes(b"previous audiobook export")
    store_export_state(
        project,
        old_options.output,
        {
            "format": "readio.audiobook-export-state",
            "export_id": prepared.export_id,
            "master_sha256": prepared.master_sha256,
            "timeline_sha256": prepared.timeline_sha256,
            "path": target_record(project, old_options.output),
            "output_sha256": hash_file(old_options.output),
        },
    )
    current_stages = {row["stage"]: row for row in project_status(project)["stages"]}
    assert current_stages["output"]["state"] == "current"
    app.projects.configure(
        project.root,
        ProjectSettings(
            audiobook_export=AudiobookExportOptions(
                output=project.root / "exports" / "new.m4b",
                title="New title",
                bitrate="192k",
            )
        ),
    )
    project = load_project(project.root)

    stages = {row["stage"]: row for row in project_status(project)["stages"]}

    assert stages["output"]["state"] == "stale"
    assert stages["output"]["reason"] == "output.stale.project_settings_changed"
