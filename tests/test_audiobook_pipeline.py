from __future__ import annotations

import json

import pytest
from audiobook_support import make_epub
from multiscope_support import make_audiobook_project
from project_support import Adapter

from readio import cli
from readio.config import ReaderSettings, ReadioConfig
from readio.engines.registry import _registry
from readio.project import load_project
from readio.selection import resolve_project_selection
from readio.stages.pipeline import preview_project, project_status, render_project
from readio.stages.planning import load_scope_plan, plan_project
from readio.stages.synthesis import synthesize_project


def test_multiscope_synthesis_opens_once_and_reuses_identical_speech(
    tmp_path, monkeypatch
) -> None:
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    project, cfg, request = make_audiobook_project(tmp_path)
    planning = plan_project(project, cfg)
    expected_scope_ids = [scope.scope.id for scope in planning.scopes]
    events = []

    result = synthesize_project(project, cfg, request=request, on_event=events.append)

    assert adapter.open_calls == 1
    assert result["rendered"] < result["reused"] + result["rendered"]
    cache_event = next(event for event in events if event.kind == "cache_scanned")
    assert cache_event.details["required"] == result["reused"] + result["rendered"]
    assert cache_event.details["scopes"] == len(expected_scope_ids)
    rendered_scope_ids = {
        scope.scope.id for scope in planning.scopes if scope.compiled.plan.segments
    }
    assert {
        event.scope_id
        for event in events
        if event.kind in {"segment_started", "unit_started"}
    } == rendered_scope_ids
    trace = json.loads(project.paths["synthesis_trace"].read_text(encoding="utf-8"))
    assert trace["schema_version"] == 3
    assert [item["scope_id"] for item in trace["plans"]] == expected_scope_ids
    assert {item["scope_id"] for item in trace["segments"]} == rendered_scope_ids
    duplicate_keys = [item["synthesis_key"] for item in trace["segments"]]
    assert len(set(duplicate_keys)) < len(duplicate_keys)
    for item in trace["segments"]:
        assert item["path"].startswith(f"synthesis/segments/{item['scope_id']}/")
        assert (project.root / item["path"]).is_file()

    cached = synthesize_project(project, cfg, request=request)
    assert cached["rendered"] == 0
    assert cached["reused"] == len(trace["segments"])
    assert adapter.open_calls == 1


def test_preview_selects_project_wide_and_render_composes_every_scope(
    tmp_path, monkeypatch
) -> None:
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    project, cfg, request = make_audiobook_project(tmp_path)
    plan_project(project, cfg)
    plan_scopes = tuple(
        (scope, load_scope_plan(project, scope))
        for scope in project.load_plan_index().scopes
    )
    selection = resolve_project_selection(plan_scopes, "first:3")
    expected_segments = sum(len(scope.segment_ids) for scope in selection.scopes)
    selected_scope_ids = {scope.scope_id for scope in selection.scopes if scope.segment_ids}
    events = []

    preview = preview_project(
        project,
        cfg,
        request=request,
        selector="first:3",
        on_event=events.append,
    )

    assert preview["rendered"] == expected_segments
    assert {
        event.scope_id
        for event in events
        if event.kind in {"segment_started", "unit_started"}
    } == selected_scope_ids
    assert adapter.open_calls == 1

    render_project(project, cfg)

    assert adapter.open_calls == 2
    timeline = json.loads(project.paths["composition_timeline"].read_text(encoding="utf-8"))
    scopes = project.document_scopes()
    assert [chapter["scope_id"] for chapter in timeline["chapters"]] == [
        scope.id for scope in scopes
    ]
    assert [chapter["source_number"] for chapter in timeline["chapters"]] == [
        scope.source_number for scope in scopes
    ]
    assert timeline["chapters"][0]["start_sample"] == 0
    assert len({item["id"] for item in timeline["layout"] if item["kind"] == "speech"}) == sum(
        len(plan.segments) for _, plan in plan_scopes
    )
    for item in timeline["layout"]:
        if item["kind"] == "speech":
            scope_id = item["scope_id"]
            segment_id = item["segment_id"]
            assert (project.root / "composition" / "parts" / scope_id / f"{segment_id}.wav").is_file()
    stages = {row["stage"]: row for row in project_status(project)["stages"]}
    assert all(stages[name]["state"] == "current" for name in ("plan", "synthesis", "composition", "output"))


def test_cli_audiobook_init_plan_and_render_end_to_end(
    tmp_path, monkeypatch, capsys
    ) -> None:
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(
        reader=ReaderSettings(engine="fake", voice="fake-voice", spacy="off")
    )
    monkeypatch.setattr(cli, "_resolved_config", lambda args: cfg)
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "novel.epub"
    output = tmp_path / "novel.readio"
    make_epub(source)

    with pytest.raises(SystemExit) as init_exit:
        cli.main(
            [
                "audiobook",
                "init",
                str(source),
                "--chapters",
                "2-4,5",
                "--json",
            ]
        )
    assert init_exit.value.code == 0
    initialized = json.loads(capsys.readouterr().out)
    assert initialized["project"] == str(output)
    assert initialized["selected_chapters"] == 4

    with pytest.raises(SystemExit) as plan_exit:
        cli.main(["plan", str(output), "--json"])
    assert plan_exit.value.code == 0
    planned = json.loads(capsys.readouterr().out)
    assert len(planned["scopes"]) == 4

    with pytest.raises(SystemExit) as render_exit:
        cli.main(["render", str(output), "--format", "wav", "--json"])
    assert render_exit.value.code == 0
    rendered = json.loads(capsys.readouterr().out)
    assert rendered["ok"] is True
    assert adapter.open_calls == 1
    project = load_project(output)
    assert project.paths["composition_master"].is_file()
    assert {row["stage"] for row in project_status(project)["stages"]} >= {
        "plan",
        "synthesis",
        "composition",
        "output",
    }
