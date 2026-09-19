from __future__ import annotations

from readio.config import ReaderSettings, ReadioConfig
from readio.document import document_from_text
from readio.stages.planning import resolve_semantic_planning


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
