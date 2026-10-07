from __future__ import annotations

import json
from pathlib import Path

import pytest
from utterplan import preflight_renderability

from readio.api import (
    ProjectPlanAttemptError,
    ProjectPlanInspectionOptions,
    ProjectPlanOptions,
    ProjectPlanRenderabilityError,
    ProjectPlanRepairOptions,
    Readio,
)
from readio.config import ReaderSettings, ReadioConfig
from readio.project import init_project, load_project
from readio.project_model import DocumentIndex, DocumentScope
from readio.stages.planning import load_primary_scope_plan, load_scope_plan


def _app() -> Readio:
    return Readio(ReadioConfig(reader=ReaderSettings(spacy="off", unit="paragraph")))


def _create_project(tmp_path: Path, text: str, *, name: str = "source"):
    source = tmp_path / f"{name}.txt"
    source.write_text(text, encoding="utf-8")
    return _app(), source


def _state_root(project) -> Path:
    attached = project.root / ".readio"
    return attached if attached.is_dir() else project.root


def _files(root: Path) -> dict[Path, bytes]:
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_first_failure_is_inspectable_with_issue_and_segment_details(tmp_path: Path) -> None:
    app, source = _create_project(tmp_path, "A spoken sentence.\n\n€")
    project = app.projects.create(source, output=tmp_path / "inspection.readio")

    with pytest.raises(ProjectPlanRenderabilityError) as caught:
        app.projects.plan(project, options=ProjectPlanOptions(renderability="strict"))

    state_root = _state_root(project)
    assert not (state_root / "plan" / "index.json").exists()
    attempt_id = caught.value.attempt_id
    assert attempt_id is not None
    attempt_dir = state_root / "plan" / "attempts" / attempt_id
    manifest = json.loads((attempt_dir / "attempt.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "blocked"
    assert manifest["activated"] is False
    assert (attempt_dir / manifest["scopes"][0]["candidate_path"]).suffix == ".toml"

    inspection = app.projects.inspect_plan(
        project,
        options=ProjectPlanInspectionOptions(issues=True, repairs=True),
    )
    assert inspection.selected == "attempt"
    assert inspection.attempt is not None
    assert inspection.attempt.attempt_id == attempt_id
    assert inspection.attempt.status == "blocked"
    assert len(inspection.issues) == 1
    issue = inspection.issues[0]
    assert issue.text == "€"
    assert issue.repair_safe is False
    assert issue.unit_id is not None

    segment = app.projects.inspect_plan(
        project,
        options=ProjectPlanInspectionOptions(
            scope_id="document",
            segment_id=issue.segment_id,
            issues=True,
            source_context=1,
        ),
    ).segments[0]
    assert segment["source"]["text"] == "€"
    assert segment["structural"]["text"] == "€"
    assert segment["spoken"]["text"] == "€"
    assert segment["unit_ids"] == [issue.unit_id]
    assert segment["renderability"]["status"] == "blocked"
    assert segment["source"]["context"]
    json.loads(json.dumps(inspection.to_dict(), ensure_ascii=False))

    active = app.projects.inspect_plan(
        project, options=ProjectPlanInspectionOptions(attempt="active")
    )
    assert active.selected == "active_plan"
    assert active.attempt is None
    assert active.active_plan_status == "missing"


def test_malformed_attempt_returns_structured_error_and_is_not_activated(tmp_path: Path) -> None:
    app, source = _create_project(tmp_path, "A spoken sentence.\n\n€", name="malformed")
    project = app.projects.create(source, output=tmp_path / "malformed.readio")
    with pytest.raises(ProjectPlanRenderabilityError) as caught:
        app.projects.plan(project, options=ProjectPlanOptions(renderability="strict"))
    assert caught.value.attempt_id is not None

    issue_path = (
        _state_root(project) / "plan" / "attempts" / caught.value.attempt_id / "issues.json"
    )
    issue_path.write_text("{not valid JSON", encoding="utf-8")
    with pytest.raises(ProjectPlanAttemptError) as malformed:
        app.projects.inspect_plan(
            project,
            options=ProjectPlanInspectionOptions(attempt=caught.value.attempt_id),
        )
    assert malformed.value.code == "planning.attempt_invalid"
    assert malformed.value.details["attempt_id"] == caught.value.attempt_id


def test_failed_attempt_preserves_active_plan_bytes(tmp_path: Path) -> None:
    app, source = _create_project(tmp_path, "A clean sentence.", name="preserve")
    project = app.projects.create(source, output=tmp_path / "preserve.readio")
    active = app.projects.plan(project, options=ProjectPlanOptions(renderability="strict"))
    assert active.activated
    state_root = _state_root(project)
    index_bytes = (state_root / "plan" / "index.json").read_bytes()
    active_path = state_root / "plan" / "document.utterplan.toml"
    active_bytes = active_path.read_bytes()

    project_document = state_root / "document" / "document.ssmd.md"
    project_document.write_text("A clean sentence.\n\n€", encoding="utf-8")
    with pytest.raises(ProjectPlanRenderabilityError):
        app.projects.plan(project, options=ProjectPlanOptions(renderability="strict"))

    assert (state_root / "plan" / "index.json").read_bytes() == index_bytes
    assert active_path.read_bytes() == active_bytes
    latest = app.projects.inspect_plan(
        project,
        options=ProjectPlanInspectionOptions(issues=True),
    )
    assert latest.selected == "attempt"
    assert latest.attempt is not None and latest.attempt.status == "blocked"
    active_view = app.projects.inspect_plan(
        project,
        options=ProjectPlanInspectionOptions(attempt="active"),
    )
    assert active_view.attempt is not None
    assert active_view.attempt.attempt_id == active.attempt_id


def test_repair_dry_run_is_read_only_then_safe_repair_activates(tmp_path: Path) -> None:
    text = "First sentence.\n\n.\n\nLast sentence."
    app, source = _create_project(tmp_path, text, name="repair")
    project = app.projects.create(source, output=tmp_path / "repair.readio")
    with pytest.raises(ProjectPlanRenderabilityError) as caught:
        app.projects.plan(project, options=ProjectPlanOptions(renderability="strict"))
    state_root = _state_root(project)
    before = _files(state_root)
    project_source = (state_root / "document" / "document.ssmd.md").read_bytes()

    preview = app.projects.repair_plan(
        project,
        options=ProjectPlanRepairOptions(dry_run=True),
    )
    assert preview.dry_run is True
    assert preview.activated is False
    assert preview.repairs > 0
    assert _files(state_root) == before

    repaired = app.projects.repair_plan(project)
    assert repaired.activated is True
    assert repaired.dry_run is False
    assert repaired.attempt.parent_attempt_id == caught.value.attempt_id
    assert repaired.repairs > 0
    assert repaired.source_files_changed is False
    assert source.read_text(encoding="utf-8") == text
    assert (state_root / "document" / "document.ssmd.md").read_bytes() == project_source
    plan = load_primary_scope_plan(load_project(project.root))
    assert preflight_renderability(plan).ok


def test_repair_blocks_mixed_safe_and_unsafe_issues_without_source_edits(tmp_path: Path) -> None:
    text = "First sentence.\n\n.\n\n€\n\nLast sentence."
    app, source = _create_project(tmp_path, text, name="mixed")
    project = app.projects.create(source, output=tmp_path / "mixed.readio")
    with pytest.raises(ProjectPlanRenderabilityError) as caught:
        app.projects.plan(project, options=ProjectPlanOptions(renderability="strict"))
    state_root = _state_root(project)
    before = _files(state_root)

    result = app.projects.repair_plan(project)

    assert result.activated is False
    assert result.attempt.status == "blocked"
    assert result.attempt.parent_attempt_id == caught.value.attempt_id
    assert {issue.reason for issue in result.issues} >= {"symbol_only"}
    assert (state_root / "plan" / "index.json").exists() is False
    assert (
        _files(state_root)[Path("document/document.ssmd.md")]
        == before[Path("document/document.ssmd.md")]
    )
    assert source.read_text(encoding="utf-8") == text


def test_repair_reuses_unchanged_successful_scopes(tmp_path: Path) -> None:
    source = tmp_path / "multiscope.txt"
    source.write_text("Initial placeholder.", encoding="utf-8")
    internal = init_project(source, tmp_path / "multiscope.readio")
    scopes = (
        DocumentScope(
            id="chapter-0001",
            kind="chapter",
            path="document/chapters/chapter-0001.md",
            input_format="markdown",
            title="Chapter One",
            source_number=1,
        ),
        DocumentScope(
            id="chapter-0002",
            kind="chapter",
            path="document/chapters/chapter-0002.md",
            input_format="markdown",
            title="Chapter Two",
            source_number=2,
        ),
    )
    source_bytes = {}
    for scope in scopes:
        path = internal.path(scope.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = (
            "Chapter One is clean.\n\nEnd of Chapter One."
            if scope.id == "chapter-0001"
            else "Chapter Two has an isolated mark.\n\n.\n\nEnd of Chapter Two."
        )
        path.write_text(body, encoding="utf-8")
        source_bytes[scope.id] = path.read_bytes()
    index = DocumentIndex(scopes=scopes, selection=(1, 2))
    internal.paths["document_index"].write_text(json.dumps(index.to_dict()), encoding="utf-8")

    app = _app()
    project = app.projects.open(internal.root)
    with pytest.raises(ProjectPlanRenderabilityError):
        app.projects.plan(project, options=ProjectPlanOptions(renderability="strict"))

    result = app.projects.repair_plan(project)

    assert result.activated is True
    assert result.repairs == 1
    assert result.reused_scopes == ("chapter-0001",)
    assert result.rebuilt_scopes == ("chapter-0002",)
    assert {scope.id: internal.path(scope.path).read_bytes() for scope in scopes} == source_bytes
    assert all(
        preflight_renderability(load_scope_plan(internal, scope)).ok
        for scope in internal.load_plan_index().scopes
    )


def test_safe_repairs_succeed_across_multiple_scopes(tmp_path: Path) -> None:
    source = tmp_path / "multi-repair.txt"
    source.write_text("Initial placeholder.", encoding="utf-8")
    internal = init_project(source, tmp_path / "multi-repair.readio")
    scopes = tuple(
        DocumentScope(
            id=f"chapter-{index:04d}",
            kind="chapter",
            path=f"document/chapters/chapter-{index:04d}.md",
            input_format="markdown",
            title=f"Chapter {index}",
            source_number=index,
        )
        for index in (1, 2)
    )
    for scope in scopes:
        path = internal.path(scope.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"Chapter {scope.source_number}.\n\n.\n\nEnd.", encoding="utf-8")
    internal.paths["document_index"].write_text(
        json.dumps(DocumentIndex(scopes=scopes, selection=(1, 2)).to_dict()), encoding="utf-8"
    )

    app = _app()
    project = app.projects.open(internal.root)
    result = app.projects.plan(project)

    assert result.activated is True
    assert result.repairs == 2
    assert len(result.scopes) == 2


def test_public_api_scene_breaks_remain_boundaries_not_renderer_speech(tmp_path: Path) -> None:
    text = (
        '---\nssmd_version: "0.9"\nlanguage: en-US\n---\n'
        "Before the first break.\n\n---\n\nMiddle text.\n\n...p\n\n"
        "After the last break.\n\nInline Before --- after.\n\nThe marker is \\---."
    )
    source = tmp_path / "scene-breaks.ssmd"
    source.write_text(text, encoding="utf-8")
    app = _app()
    project = app.projects.create(source, output=tmp_path / "scene-breaks.readio")

    result = app.projects.plan(project, options=ProjectPlanOptions(renderability="strict"))

    assert result.activated is True
    plan = load_primary_scope_plan(load_project(project.root))
    assert preflight_renderability(plan).ok
    segment_texts = [segment.text for segment in plan.segments]
    assert "---" not in segment_texts
    assert "...p" not in segment_texts
    assert "Inline Before --- after." in segment_texts
    assert "The marker is ---." in segment_texts
    scene_boundaries = [
        boundary
        for boundary in plan.boundaries
        if boundary.kind == "explicit" and boundary.strength == "x-strong"
    ]
    assert len(scene_boundaries) == 2
    assert any(boundary.attrs.get("semantic") == "scene_break" for boundary in scene_boundaries)
    assert source.read_text(encoding="utf-8") == text
