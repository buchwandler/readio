from __future__ import annotations

import json

from readio.config import ReaderSettings, ReadioConfig
from readio.document import document_from_text
from readio.project import init_project
from readio.stages.planning import load_scope_plan, plan_project, resolve_semantic_planning


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


def test_project_ssmd_plan_preserves_semantics_and_metadata(tmp_path):
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
        '<div voice="narrator">\nHello. ...600ms\nWorld.\n</div>\n\n'
        '<div voice="guest">\nReply.\n</div>\n',
        encoding="utf-8",
    )
    project = init_project(source, tmp_path / "episode.readio")
    compiled = plan_project(project, ReadioConfig())
    plan = load_scope_plan(project)
    metadata = json.loads(project.paths["document_metadata"].read_text(encoding="utf-8"))

    assert project.document().format == "ssmd"
    assert metadata["input_format"] == "ssmd"
    assert metadata["document_format"] == "ssmd"
    assert plan.config["document_format"] == "ssmd"
    spoken_text = "\n".join(segment["text"] for segment in plan.to_dict()["segments"])
    assert "<div" not in spoken_text
    assert "</div>" not in spoken_text
    assert all(value not in spoken_text for value in ("180ms", "450ms", "200ms", "600ms"))
    assert any(
        boundary["seconds"] == 0.6 for boundary in plan.to_dict()["boundaries"]
    )
    assert {
        segment["directives"]["voice"]["reference"]
        for segment in plan.to_dict()["segments"]
    } == {"narrator", "guest"}
    assert compiled.plan_id == plan.plan_id


def test_project_document_legacy_metadata_infers_semantic_format(tmp_path):
    source = tmp_path / "episode.ssmd"
    source.write_text("<div voice=\"narrator\">Hello.</div>", encoding="utf-8")
    project = init_project(source, tmp_path / "episode.readio")
    metadata = json.loads(project.paths["document_metadata"].read_text(encoding="utf-8"))
    metadata.pop("document_format")
    project.paths["document_metadata"].write_text(json.dumps(metadata), encoding="utf-8")

    assert project.document().format == "ssmd"


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
