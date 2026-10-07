from __future__ import annotations

import json

import pytest
from utterplan import preflight_renderability

from readio.config import ReaderSettings, ReadioConfig
from readio.errors import ProjectPlanRenderabilityError
from readio.project import init_project
from readio.project_model import DocumentIndex, DocumentScope
from readio.stages.planning import (
    _legacy_semantic_planner_fingerprint,
    _plan_artifact_status,
    load_primary_scope_plan,
    plan_project,
)


def _multi_scope_project(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("A multi-chapter book.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    scopes = (
        DocumentScope(
            id="chapter-0002",
            kind="chapter",
            path="document/chapters/chapter-0002.md",
            input_format="markdown",
            title="Chapter Two",
            source_number=2,
        ),
        DocumentScope(
            id="chapter-0003",
            kind="chapter",
            path="document/chapters/chapter-0003.md",
            input_format="markdown",
            title="Chapter Three",
            source_number=3,
        ),
    )
    for scope, body in zip(
        scopes,
        ("# Chapter Two\n\nFirst text.", "# Chapter Three\n\nSecond text."),
    ):
        path = project.path(scope.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    index = DocumentIndex(scopes=scopes, selection=(2, 3))
    project.paths["document_index"].write_text(json.dumps(index.to_dict()), encoding="utf-8")
    return project


def test_strict_project_failure_aggregates_issues_without_replacing_plans(tmp_path):
    project = _multi_scope_project(tmp_path)
    config = ReadioConfig(reader=ReaderSettings(spacy="off", unit="paragraph"))
    previous = plan_project(project, config)
    previous_index = project.paths["plan_index"].read_bytes()
    artifact_paths = {
        item.scope.id: project.state_root / "plan" / item.scope.path for item in previous.scopes
    }
    previous_artifacts = {scope_id: path.read_bytes() for scope_id, path in artifact_paths.items()}

    project.path("document/chapters/chapter-0002.md").write_text(
        "# Chapter Two\n\nFirst text.\n\n.\n\n...\n\nLast text.", encoding="utf-8"
    )
    project.path("document/chapters/chapter-0003.md").write_text(
        "# Chapter Three\n\nFirst text.\n\n€\n\n?\n\nLast text.", encoding="utf-8"
    )
    progress = []

    with pytest.raises(ProjectPlanRenderabilityError) as caught:
        plan_project(
            project,
            config,
            renderability_mode="strict",
            on_progress=progress.append,
        )

    error = caught.value
    assert error.code == "planning.not_renderable"
    assert error.renderability_mode == "strict"
    assert len(error.issues) == 4
    assert {issue["scope_id"] for issue in error.issues} == {"chapter-0002", "chapter-0003"}
    assert {issue["reason"] for issue in error.issues} == {
        "punctuation_only",
        "symbol_only",
    }
    for issue in error.issues:
        assert issue["code"].startswith("renderability.")
        assert issue["source_path"] in {
            "document/chapters/chapter-0002.md",
            "document/chapters/chapter-0003.md",
        }
        assert issue["line"] is not None and issue["column"] is not None
        assert issue["source_excerpt"]
        assert issue["segment_id"] and issue["text"]
        assert issue["repair_command"] == "readio plan repair ."
    assert error.details["issues"] == list(error.issues)
    assert error.details["repair_command"] == "readio plan repair ."
    attempt_id = error.details["attempt_id"]
    attempt_dir = project.state_root / "plan" / "attempts" / attempt_id
    attempt = json.loads((attempt_dir / "attempt.json").read_text(encoding="utf-8"))
    assert attempt["status"] == "blocked"
    assert attempt["activated"] is False
    assert len(attempt["scopes"]) == 2
    assert len(json.loads((attempt_dir / "issues.json").read_text(encoding="utf-8"))["issues"]) == 4
    assert project.paths["plan_index"].read_bytes() == previous_index
    assert {
        scope_id: path.read_bytes() for scope_id, path in artifact_paths.items()
    } == previous_artifacts
    assert [event.scope_id for event in progress if event.kind == "scope.failed"] == [
        "chapter-0002",
        "chapter-0003",
    ]
    assert [event.scope_id for event in progress if event.kind == "renderability.failed"] == [
        "chapter-0002",
        "chapter-0003",
    ]


def test_repair_mode_is_one_shot_reports_diagnostics_and_persists_strict_valid_plan(tmp_path):
    source = tmp_path / "repair.txt"
    source.write_text("First sentence.\n\n.\n\nSecond sentence.", encoding="utf-8")
    project = init_project(source, tmp_path / "repair.readio")
    config = ReadioConfig(reader=ReaderSettings(spacy="off", unit="paragraph"))
    original_reader = config.reader
    progress = []

    result = plan_project(
        project,
        config,
        renderability_mode="repair",
        on_progress=progress.append,
    )

    assert result.renderability_mode == "repair"
    assert result.renderability_guaranteed is True
    assert result.repairs > 0
    assert any(item["code"] == "planning.renderability.repaired" for item in result.diagnostics)
    assert result.attempt_id is not None
    attempt = json.loads(
        (project.state_root / "plan" / "attempts" / result.attempt_id / "attempt.json").read_text(
            encoding="utf-8"
        )
    )
    assert attempt["status"] == "repaired"
    assert attempt["activated"] is True
    assert config.reader is original_reader
    plan = load_primary_scope_plan(project)
    assert plan.config["renderability_mode"] == "repair"
    assert plan.document_metadata["planning"]["renderability"]["guaranteed"] is True
    assert preflight_renderability(plan).ok
    assert source.read_text(encoding="utf-8") == "First sentence.\n\n.\n\nSecond sentence."
    assert any(event.kind == "renderability.started" for event in progress)
    completed = [event for event in progress if event.kind == "renderability.completed"]
    assert completed and completed[0].details["mode"] == "repair"
    assert completed[0].details["repair_count"] == result.repairs


def test_unsafe_repair_failure_is_structured_and_writes_no_plan(tmp_path):
    source = tmp_path / "unsafe.txt"
    source.write_text("First sentence.\n\n€\n\nSecond sentence.", encoding="utf-8")
    project = init_project(source, tmp_path / "unsafe.readio")

    with pytest.raises(ProjectPlanRenderabilityError) as caught:
        plan_project(
            project,
            ReadioConfig(reader=ReaderSettings(spacy="off", unit="paragraph")),
            renderability_mode="repair",
        )

    assert caught.value.renderability_mode == "repair"
    assert [issue["reason"] for issue in caught.value.issues] == ["symbol_only"]
    assert not project.paths["plan_index"].exists()
    assert not tuple((project.state_root / "plan").rglob("*.utterplan.toml"))
    latest = json.loads(
        (project.state_root / "plan" / "attempts" / "latest.json").read_text(encoding="utf-8")
    )
    attempt_dir = project.state_root / "plan" / "attempts" / latest["attempt_id"]
    attempt = json.loads((attempt_dir / "attempt.json").read_text(encoding="utf-8"))
    assert attempt["status"] == "blocked"
    assert attempt["activated"] is False
    assert attempt["scopes"][0]["status"] == "blocked"
    assert (attempt_dir / attempt["scopes"][0]["candidate_path"]).is_file()


def test_first_strict_failure_persists_attempt_without_creating_active_plan(tmp_path):
    source = tmp_path / "first-failure.txt"
    source.write_text("A spoken sentence.\n\n€", encoding="utf-8")
    project = init_project(source, tmp_path / "first-failure.readio")

    with pytest.raises(ProjectPlanRenderabilityError):
        plan_project(
            project,
            ReadioConfig(reader=ReaderSettings(spacy="off")),
            renderability_mode="strict",
        )

    latest = json.loads(
        (project.state_root / "plan" / "attempts" / "latest.json").read_text(encoding="utf-8")
    )
    attempt_dir = project.state_root / "plan" / "attempts" / latest["attempt_id"]
    attempt = json.loads((attempt_dir / "attempt.json").read_text(encoding="utf-8"))
    assert attempt["status"] == "blocked"
    assert attempt["issue_count"] > 0
    assert attempt["activated"] is False
    assert not project.paths["plan_index"].exists()


def test_unicode_lexical_numbers_remain_renderable(tmp_path):
    source = tmp_path / "unicode-lexical.ssmd"
    original = "---\ntitle: Unicode lexical content\n---\nCafé déjà vu, ٣١."
    source.write_text(original, encoding="utf-8")
    project = init_project(source, tmp_path / "unicode-lexical.readio")

    plan_project(project, ReadioConfig(reader=ReaderSettings(spacy="off")))
    plan = load_primary_scope_plan(project)

    assert preflight_renderability(plan).ok
    assert "Café" in plan.texts.spoken
    assert "thirty one" in plan.texts.spoken
    assert all("Unicode lexical content" not in segment.text for segment in plan.segments)
    assert source.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    ("name", "scene_break"),
    (("legacy", "---"), ("canonical", "...p")),
)
def test_scene_break_forms_do_not_create_unrenderable_segments(tmp_path, name, scene_break):
    source = tmp_path / f"{name}-scene-break.ssmd"
    original = (
        "---\ntitle: Scene-boundary regression\n---\n"
        f"Before scene.\n\n{scene_break}\n\nAfter scene."
    )
    source.write_text(original, encoding="utf-8")
    project = init_project(source, tmp_path / f"{name}-scene-break.readio")

    plan_project(project, ReadioConfig(reader=ReaderSettings(spacy="off")))
    plan = load_primary_scope_plan(project)

    assert preflight_renderability(plan).ok
    assert [segment.text for segment in plan.segments] == ["Before scene.", "After scene."]
    assert all("Scene-boundary regression" not in segment.text for segment in plan.segments)
    assert source.read_text(encoding="utf-8") == original


def test_retry_reuses_unchanged_persisted_scope_attempts(tmp_path):
    project = _multi_scope_project(tmp_path)
    config = ReadioConfig(reader=ReaderSettings(spacy="off", unit="paragraph"))

    first = plan_project(project, config)
    second = plan_project(project, config)

    assert set(first.rebuilt_scopes) == {"chapter-0002", "chapter-0003"}
    assert second.reused_scopes == ("chapter-0002", "chapter-0003")
    assert second.rebuilt_scopes == ()
    attempt = json.loads(
        (project.state_root / "plan" / "attempts" / second.attempt_id / "attempt.json").read_text(
            encoding="utf-8"
        )
    )
    assert all(scope["reused"] for scope in attempt["scopes"])
    assert all(scope["source_attempt_id"] == first.attempt_id for scope in attempt["scopes"])


def test_failed_scope_leaves_completed_attempt_scopes_available(tmp_path, monkeypatch):
    from readio.stages import planning

    project = _multi_scope_project(tmp_path)
    config = ReadioConfig(reader=ReaderSettings(spacy="off", unit="paragraph"))
    compile_scope = planning.compile_project_scope

    def fail_second_scope(project, cfg, scope, document, **kwargs):
        if scope.id == "chapter-0003":
            raise RuntimeError("synthetic planner interruption")
        return compile_scope(project, cfg, scope, document, **kwargs)

    monkeypatch.setattr(planning, "compile_project_scope", fail_second_scope)
    with pytest.raises(RuntimeError, match="synthetic planner interruption"):
        plan_project(project, config)

    latest = json.loads(
        (project.state_root / "plan" / "attempts" / "latest.json").read_text(encoding="utf-8")
    )
    attempt_dir = project.state_root / "plan" / "attempts" / latest["attempt_id"]
    attempt = json.loads((attempt_dir / "attempt.json").read_text(encoding="utf-8"))
    assert attempt["status"] == "incomplete"
    assert attempt["activated"] is False
    assert [scope["status"] for scope in attempt["scopes"]] == ["renderable", "failed"]
    first_candidate = attempt_dir / attempt["scopes"][0]["candidate_path"]
    assert first_candidate.is_file()
    assert not project.paths["plan_index"].exists()


def test_legacy_mode_sensitive_plan_fingerprints_remain_current(tmp_path):
    source = tmp_path / "legacy-plan.txt"
    source.write_text("A spoken sentence.", encoding="utf-8")
    project = init_project(source, tmp_path / "legacy-plan.readio")
    plan_project(
        project,
        ReadioConfig(reader=ReaderSettings(spacy="off")),
        renderability_mode="strict",
    )
    plan = load_primary_scope_plan(project)

    index_path = project.paths["plan_index"]
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["scopes"][0]["semantic_planner_fingerprint"] = _legacy_semantic_planner_fingerprint(
        plan.config
    )
    index_path.write_text(json.dumps(index), encoding="utf-8")

    assert _plan_artifact_status(project, "text")["state"] == "current"
