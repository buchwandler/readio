from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from project_support import Adapter, request

from readio.api import InvalidRendererSegmentError
from readio.config import ReaderSettings, ReadioConfig
from readio.engines.registry import _registry
from readio.project import init_project
from readio.stages import synthesis as synthesis_stage
from readio.stages.planning import plan_project


def test_punctuation_only_fallback_is_narrow_and_media_segments_are_excluded() -> None:
    plan = SimpleNamespace(tokens=())
    for text in (".", ".)", ",", "!", "?”", " \t.\n"):
        segment = SimpleNamespace(text=text, token_indices=(), directives=None)
        assert synthesis_stage._is_punctuation_only_segment(plan, segment) == (True, [])

    for text in ("$", "+", "€", "😀", "word."):
        segment = SimpleNamespace(text=text, token_indices=(), directives=None)
        assert synthesis_stage._is_punctuation_only_segment(plan, segment) == (False, [])

    audio_segment = SimpleNamespace(
        text=".",
        token_indices=(),
        directives=SimpleNamespace(audio=object()),
    )
    assert synthesis_stage._is_punctuation_only_segment(plan, audio_segment) == (False, [])


def test_token_pos_is_preferred_when_token_metadata_is_complete() -> None:
    plan = SimpleNamespace(tokens=(SimpleNamespace(pos="PROPN"),))
    segment = SimpleNamespace(text=".", token_indices=(0,), directives=None)
    assert synthesis_stage._is_punctuation_only_segment(plan, segment) == (False, ["PROPN"])

    punctuation_plan = SimpleNamespace(tokens=(SimpleNamespace(pos="PUNCT"),))
    assert synthesis_stage._is_punctuation_only_segment(punctuation_plan, segment) == (
        True,
        ["PUNCT"],
    )


def test_malformed_selected_plan_fails_before_engine_session_opens(tmp_path, monkeypatch) -> None:
    adapter = Adapter()
    adapter.synthesize_calls = 0
    original_open = adapter.open

    def tracked_open(selection):
        @contextmanager
        def session_context():
            with original_open(selection) as session:

                class CountingSession:
                    def synthesize(self, speech_request):
                        adapter.synthesize_calls += 1
                        return session.synthesize(speech_request)

                yield CountingSession()

        return session_context()

    adapter.open = tracked_open
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice", spacy="off"))
    source = tmp_path / "malformed.txt"
    source.write_text("A sentence to plan.", encoding="utf-8")
    project = init_project(source, tmp_path / "malformed.readio")
    plan_project(project, cfg)
    plan_scope = project.load_plan_index().scopes[0]
    valid_plan = synthesis_stage.load_scope_plan(project, plan_scope)
    first = valid_plan.segments[0]
    malformed_segment = replace(first, text=".", token_indices=())
    malformed_plan = replace(valid_plan, segments=(malformed_segment, *valid_plan.segments[1:]))
    monkeypatch.setattr(
        synthesis_stage,
        "load_scope_plan",
        lambda _project, _scope: malformed_plan,
    )

    with pytest.raises(InvalidRendererSegmentError) as raised:
        synthesis_stage.synthesize_project(project, cfg, request=request(project))

    error = raised.value
    assert error.code == "synthesis.invalid_renderer_segment"
    assert error.details["scope_id"] == "document"
    assert error.details["unit_id"] == valid_plan.units[0].id
    assert error.details["segment_id"] == first.id
    assert error.details["text"] == "."
    assert error.details["token_pos"] == []
    assert error.details["reason"] == "punctuation_only_renderer_segment"
    assert "punctuation-only '.'" in str(error)
    assert adapter.open_calls == 0
    assert adapter.synthesize_calls == 0


LONG_RENDERER_TEXT = (
    "This is a deliberately long renderer segment whose complete content must remain visible "
    "through every synthesis progress surface, including embedded\nwhitespace and tabs\t. "
    "FULL_TEXT_TAIL_SENTINEL"
)


def _long_text_project(tmp_path, monkeypatch):
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice", spacy="off"))
    source = tmp_path / "long-text.txt"
    source.write_text("A sentence to plan.", encoding="utf-8")
    project = init_project(source, tmp_path / "long-text.readio")
    plan_project(project, cfg)
    plan_scope = project.load_plan_index().scopes[0]
    valid_plan = synthesis_stage.load_scope_plan(project, plan_scope)
    first = valid_plan.segments[0]
    long_segment = replace(first, text=LONG_RENDERER_TEXT, token_indices=())
    long_plan = replace(valid_plan, segments=(long_segment, *valid_plan.segments[1:]))
    monkeypatch.setattr(
        synthesis_stage,
        "load_scope_plan",
        lambda _project, _scope: long_plan,
    )
    return adapter, cfg, project, long_segment


def test_internal_synthesis_events_preserve_exact_long_renderer_text(tmp_path, monkeypatch) -> None:
    adapter, cfg, project, segment = _long_text_project(tmp_path, monkeypatch)
    events = []

    synthesis_stage.synthesize_project(
        project, cfg, request=request(project), on_event=events.append
    )

    segment_events = [
        event
        for event in events
        if event.segment_id == segment.id and event.kind in {"segment_started", "segment_finished"}
    ]
    assert [event.kind for event in segment_events] == ["segment_started", "segment_finished"]
    assert [event.text for event in segment_events] == [LONG_RENDERER_TEXT, LONG_RENDERER_TEXT]
    assert adapter.open_calls == 1


def test_backend_failure_includes_complete_segment_context_and_cause(tmp_path, monkeypatch) -> None:
    from readio.api import EngineBackendError

    _adapter, cfg, project, segment = _long_text_project(tmp_path, monkeypatch)
    backend_error = RuntimeError("backend failed")

    def fail_render(*_args, **_kwargs):
        raise backend_error

    monkeypatch.setattr("readio.rendering.render_atomic_request", fail_render)
    with pytest.raises(EngineBackendError) as raised:
        synthesis_stage.synthesize_project(project, cfg, request=request(project))

    error = raised.value
    assert error.__cause__ is backend_error
    assert error.details["scope_id"] == "document"
    assert (
        error.details["unit_id"]
        == synthesis_stage.load_scope_plan(project, project.load_plan_index().scopes[0]).units[0].id
    )
    assert error.details["segment_id"] == segment.id
    assert error.details["segment_index"] == 0
    assert error.details["text"] == LONG_RENDERER_TEXT
    assert error.details["engine"] == "fake"
    assert error.details["target_id"]
    assert "FULL_TEXT_TAIL_SENTINEL" in str(error)
