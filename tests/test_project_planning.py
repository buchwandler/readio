from __future__ import annotations

import json
from pathlib import Path

import pytest
from document_support import write_test_pdf

from readio.config import ReaderSettings, ReadioConfig
from readio.document import document_from_text
from readio.project import init_project
from readio.project_model import DocumentIndex, DocumentScope
from readio.stages.planning import (
    load_primary_scope_plan,
    load_scope_plan,
    plan_project,
    resolve_semantic_planning,
    semantic_status,
)


def test_semantic_planning_does_not_resolve_engine_and_is_acoustic_invariant(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("engine resolution must not run")

    monkeypatch.setattr("readio.engines.registry.get_engine", fail)
    document = document_from_text("Hello.\n\nWorld.")
    first = resolve_semantic_planning(
        ReadioConfig(reader=ReaderSettings(engine="fake", voice="one")), document
    )
    second = resolve_semantic_planning(
        ReadioConfig(reader=ReaderSettings(engine="other", voice="two")), document
    )
    assert first.compiled.plan_id == second.compiled.plan_id
    assert first.compiled.plan.units


def test_semantic_policy_changes_identity():
    document = document_from_text("Hello.\n\nWorld.")
    first = resolve_semantic_planning(
        ReadioConfig(reader=ReaderSettings(unit="sentence")), document
    )
    second = resolve_semantic_planning(
        ReadioConfig(reader=ReaderSettings(unit="paragraph")), document
    )
    assert first.compiled.plan_id != second.compiled.plan_id


def test_project_ssmd_plan_preserves_semantics_and_conversion_metadata(tmp_path):
    source = tmp_path / "episode.ssmd"
    source.write_text(
        "---\n"
        "title: Test\n"
        "pause_defaults:\n"
        "  sentence: 180ms\n"
        "  paragraph: 450ms\n"
        "  voice_change: 200ms\n"
        "voice_bindings:\n"
        "  kokoro:\n"
        "    narrator: af_heart\n"
        "    guest: af_bella\n"
        "---\n\n"
        ':::{voice="narrator"}\nHello. ...600ms\nWorld.\n:::\n\n'
        ':::{voice="guest"}\nReply.\n:::\n',
        encoding="utf-8",
    )
    project = init_project(source, tmp_path / "episode.readio")
    compiled = plan_project(project, ReadioConfig())
    plan = load_primary_scope_plan(project)
    document_index = project.load_document_index()
    scope = document_index.scopes[0]

    assert project.manifest.source_format == "ssmd"
    assert scope.input_format == "ssmd"
    assert scope.path == "document/document.ssmd.md"
    assert document_index.metadata["conversion"]["tool"] == "ssmdconvert"
    assert document_index.metadata["conversion"]["source_format"] == "ssmd"
    assert not project.paths["document_metadata"].exists()
    assert project.document().format == "ssmd"
    assert plan.config["document_format"] == "ssmd"
    spoken_text = "\n".join(segment["text"] for segment in plan.to_dict()["segments"])
    assert "<div" not in spoken_text
    assert "</div>" not in spoken_text
    assert all(value not in spoken_text for value in ("180ms", "450ms", "200ms", "600ms"))
    assert any(boundary["seconds"] == 0.6 for boundary in plan.to_dict()["boundaries"])
    assert {
        segment["directives"]["voice"]["reference"] for segment in plan.to_dict()["segments"]
    } == {"narrator", "guest"}
    assert compiled.scopes[0].compiled.plan_id == plan.plan_id


def test_schema_v2_scope_format_is_semantic_not_source_provenance(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "episode.readio")
    scope = project.document_scopes()[0]

    assert project.manifest.source_format == "text"
    assert scope.input_format == "ssmd"
    assert scope.path == "document/document.ssmd.md"
    assert project.document().format == "ssmd"
    assert not project.paths["document_metadata"].exists()


def test_pdf_project_uses_editable_ssmd_without_reopening_source(tmp_path, monkeypatch):
    source = tmp_path / "report.pdf"
    write_test_pdf(source, "Original PDF text")
    project = init_project(source, tmp_path / "report.readio")
    scope = project.document_scopes()[0]
    source_snapshot = project.paths["source"]
    assert project.manifest.source_format == "pdf"
    assert scope.input_format == "ssmd"
    assert scope.path == "document/document.ssmd.md"

    original_read_text = Path.read_text

    def reject_source_text(path, *args, **kwargs):
        if path.resolve() == source_snapshot.resolve():
            raise AssertionError("planning must not decode the original PDF source")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_source_text)
    plan_project(project, ReadioConfig(reader=ReaderSettings(spacy="off")))
    initial_plan_scope = project.load_plan_index().scopes[0]
    statuses = {row["stage"]: row for row in semantic_status(project)}
    assert statuses["source"]["state"] == "current"
    assert statuses["document"]["state"] == "current"
    assert statuses["plan"]["state"] == "current"

    source_snapshot.write_bytes(b"changed source bytes")
    statuses = {row["stage"]: row for row in semantic_status(project)}
    assert statuses["source"]["reason"] == "source.stale.hash_changed"
    assert statuses["document"]["state"] == "current"
    assert statuses["plan"]["state"] == "current"

    semantic_path = project.path(scope.path)
    semantic_path.write_text(
        "---\nssmd_version: '0.9'\n---\nEdited semantic SSMD.", encoding="utf-8"
    )
    statuses = {row["stage"]: row for row in semantic_status(project)}
    assert statuses["source"]["state"] == "stale"
    assert statuses["document"]["state"] == "current"
    assert statuses["plan"]["reason"] == "plan.stale.document_changed"

    monkeypatch.setattr(
        "readio.integrations.ssmdconvert.ssmdconvert_convert",
        lambda *_args, **_kwargs: pytest.fail("planning must not reconvert the source"),
    )
    plan_project(project, ReadioConfig(reader=ReaderSettings(spacy="off")))
    assert project.load_plan_index().scopes[0].document_sha256 != initial_plan_scope.document_sha256
    assert semantic_status(project)[-1]["state"] == "current"


def test_wrong_semantic_plan_format_is_stale_with_actionable_reason(tmp_path):
    source = tmp_path / "episode.ssmd"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "episode.readio")
    plan_project(project, ReadioConfig())
    plan_path = project.root / "plan" / "document.utterplan.json"
    from utterplan import UtterancePlan

    plan = UtterancePlan.load(plan_path)
    plan.config["document_format"] = "plain"
    plan = plan.with_identity()
    plan_path.write_text(plan.to_json(), encoding="utf-8")
    index = json.loads(project.paths["plan_index"].read_text(encoding="utf-8"))
    import hashlib

    index["scopes"][0]["plan_id"] = plan.plan_id
    index["scopes"][0]["sha256"] = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    project.paths["plan_index"].write_text(json.dumps(index), encoding="utf-8")

    from readio.stages.planning import semantic_status

    row = semantic_status(project)[-1]
    assert row["state"] == "stale"
    assert row["reason"] == "plan.stale.document_format_mismatch"
    assert row["stored"] == "plain"
    assert row["expected"] == "ssmd"


def _multi_scope_project(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("Source provenance.", encoding="utf-8")
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
        scopes, ("# Chapter Two\n\nFirst text.", "# Chapter Three\n\nSecond text.")
    ):
        path = project.path(scope.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    index = DocumentIndex(scopes=scopes, selection=(2, 3))
    project.paths["document_index"].write_text(json.dumps(index.to_dict()), encoding="utf-8")
    return project


def test_project_planning_plans_all_document_scopes_and_tracks_input_hashes(tmp_path):
    project = _multi_scope_project(tmp_path)
    result = plan_project(project, ReadioConfig(reader=ReaderSettings(spacy="off")))
    initial = project.load_plan_index().scopes

    assert [item.scope.id for item in result.scopes] == ["chapter-0002", "chapter-0003"]
    assert [item.id for item in initial] == ["chapter-0002", "chapter-0003"]
    assert all(item.document_sha256 for item in initial)
    assert all(item.plan_id for item in initial)
    assert all(load_scope_plan(project, item).schema_version == 3 for item in initial)

    project.path("document/chapters/chapter-0003.md").write_text(
        "# Chapter Three\n\nUpdated text.", encoding="utf-8"
    )
    stale = semantic_status(project)[-1]
    assert stale["state"] == "stale"
    assert stale["reason"] == "plan.stale.document_changed"
    assert stale["scope_id"] == "chapter-0003"

    updated = plan_project(project, ReadioConfig(reader=ReaderSettings(spacy="off")))
    refreshed = project.load_plan_index().scopes
    assert [item.id for item in refreshed] == ["chapter-0002", "chapter-0003"]
    assert refreshed[0].plan_id == initial[0].plan_id
    assert refreshed[0].document_sha256 == initial[0].document_sha256
    assert refreshed[1].plan_id != initial[1].plan_id
    assert refreshed[1].document_sha256 != initial[1].document_sha256
    assert len(updated.scopes) == 2


def test_project_planning_does_not_replace_index_when_scope_compile_fails(tmp_path, monkeypatch):
    project = _multi_scope_project(tmp_path)
    from readio.stages import planning

    def fail_second(project, cfg, scope, document):
        if scope.id == "chapter-0003":
            raise ValueError("compile failed")
        return original(project, cfg, scope, document)

    original = planning.compile_project_scope
    monkeypatch.setattr(planning, "compile_project_scope", fail_second)
    with pytest.raises(ValueError, match="compile failed"):
        plan_project(project, ReadioConfig(reader=ReaderSettings(spacy="off")))
    assert not project.paths["plan_index"].exists()
