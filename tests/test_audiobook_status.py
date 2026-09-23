from __future__ import annotations

import json
from collections import Counter

from multiscope_support import make_audiobook_project
from project_support import Adapter

from readio.engines.registry import _registry
from readio.stages.pipeline import project_status, render_project
from readio.stages.planning import plan_project


def test_status_reports_provenance_scope_and_aggregate_cache_freshness(
    tmp_path, monkeypatch
) -> None:
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    project, cfg, _ = make_audiobook_project(tmp_path)
    original_epub = project.paths["source"].read_bytes()
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
    project.root.joinpath(missing["cache_path"]).unlink()
    stale = {row["stage"]: row for row in project_status(project)["stages"]}
    assert stale["synthesis"]["state"] == "stale"
    assert stale["synthesis"]["missing"] == 1
    assert stale["composition"]["blocked_by"] == "synthesis"
    assert stale["output"]["blocked_by"] == "composition"

    project.paths["source"].write_bytes(original_epub + b"changed")
    provenance = {row["stage"]: row for row in project_status(project)["stages"]}
    assert provenance["source"]["reason"] == "source.stale.hash_changed"
    assert "reinitialize" in next(
        issue["message"]
        for issue in project_status(project)["issues"]
        if issue["stage"] == "source"
    )

    project.paths["source"].write_bytes(original_epub)
    changed_scope = project.document_scopes()[2]
    chapter_path = project.path(changed_scope.path)
    chapter_path.write_text(
        chapter_path.read_text(encoding="utf-8") + "\nChanged.", encoding="utf-8"
    )
    changed = {row["stage"]: row for row in project_status(project)["stages"]}
    assert changed["plan"]["reason"] == "plan.stale.document_changed"
    assert changed["plan"]["scope_id"] == changed_scope.id
    assert changed["synthesis"]["blocked_by"] == "plan"
