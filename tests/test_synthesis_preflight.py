from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace

import pytest
from project_support import Adapter, request

from readio.api import (
    InvalidRendererSegmentError,
    InvalidStoredPlanError,
    Readio,
    SynthesisPreflightError,
    SynthesisRequest,
)
from readio.config import ReaderSettings, ReadioConfig
from readio.engines.base import RequestMeasure
from readio.engines.registry import _registry
from readio.plan import PlanDiagnostic
from readio.project import init_project
from readio.rendering.lowering import LoweringError
from readio.stages import synthesis as synthesis_stage
from readio.stages.planning import plan_project


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


def _persist_nonrenderable_plan(project, scope, plan) -> None:
    first = plan.segments[0]
    spoken = list(plan.texts.spoken)
    punctuation = "." * (first.spoken_end - first.spoken_start)
    spoken[first.spoken_start : first.spoken_end] = punctuation
    invalid_segment = replace(first, text=punctuation)
    invalid_plan = replace(
        plan,
        texts=replace(plan.texts, spoken="".join(spoken)),
        segments=(invalid_segment, *plan.segments[1:]),
    ).with_identity()
    artifact_path = project.state_root / "plan" / scope.path
    artifact_path.write_text(invalid_plan.to_json(), encoding="utf-8")
    plan_index = project.load_plan_index()
    updated_scope = replace(
        scope,
        plan_id=invalid_plan.plan_id,
        sha256=hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
    )
    updated_index = replace(plan_index, scopes=(updated_scope,))
    project.paths["plan_index"].write_text(json.dumps(updated_index.to_dict()), encoding="utf-8")


def test_stored_nonrenderable_plan_fails_before_synthesis_routing(tmp_path, monkeypatch) -> None:
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice", spacy="off"))
    source = tmp_path / "stored-invalid.txt"
    source.write_text("A sentence to plan.", encoding="utf-8")
    project = init_project(source, tmp_path / "stored-invalid.readio")
    plan_project(project, cfg)
    scope = project.load_plan_index().scopes[0]
    valid_plan = synthesis_stage.load_scope_plan(project, scope)
    _persist_nonrenderable_plan(project, scope, valid_plan)
    cache_dir = project.state_root / "synthesis" / "cache"
    cache_before = (
        {
            path.relative_to(cache_dir): path.read_bytes()
            for path in cache_dir.rglob("*")
            if path.is_file()
        }
        if cache_dir.exists()
        else {}
    )

    monkeypatch.setattr(
        synthesis_stage,
        "resolve_project_synthesis",
        lambda *_args, **_kwargs: pytest.fail("invalid plans must fail before route resolution"),
    )
    monkeypatch.setattr(
        synthesis_stage,
        "_build_project_synthesis_route",
        lambda *_args, **_kwargs: pytest.fail("invalid plans must fail before route building"),
    )
    with pytest.raises(InvalidStoredPlanError) as caught:
        synthesis_stage.synthesize_project(project, cfg, request=request(project))

    error = caught.value
    assert error.code == "plan.invalid.not_renderable"
    assert error.details["scope_id"] == scope.id
    assert error.details["validation_code"] == "segment.not_renderable"
    assert error.details["action"] == "readio plan build ."
    assert "readio plan build ." in str(error)
    assert adapter.open_calls == 0
    assert {
        path.relative_to(cache_dir): path.read_bytes()
        for path in cache_dir.rglob("*")
        if path.is_file()
    } == cache_before


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


def _two_segment_project(tmp_path, monkeypatch, adapter):
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice", spacy="off"))
    source = tmp_path / "two-segments.txt"
    source.write_text("First sentence. Second sentence.", encoding="utf-8")
    project = init_project(source, tmp_path / "two-segments.readio")
    plan_project(project, cfg)
    scope = project.load_plan_index().scopes[0]
    plan = synthesis_stage.load_scope_plan(project, scope)
    assert len(plan.segments) == 2
    return cfg, project, scope, plan


def test_all_selected_lowering_failures_aggregate_before_audio_or_session(
    tmp_path, monkeypatch
) -> None:
    adapter = Adapter()
    cfg, project, _scope, plan = _two_segment_project(tmp_path, monkeypatch, adapter)
    failed_ids = {segment.id for segment in plan.segments}
    original_lower = synthesis_stage.lower_segment
    lower_calls: list[str] = []

    def fail_selected(plan_value, segment, selection, capabilities):
        lower_calls.append(segment.id)
        if segment.id in failed_ids:
            raise LoweringError(
                PlanDiagnostic(
                    code="render.test_target_failure",
                    severity="error",
                    message=f"Cannot lower {segment.id}.",
                    field="render.segment.test",
                )
            )
        return original_lower(plan_value, segment, selection, capabilities)

    monkeypatch.setattr(synthesis_stage, "lower_segment", fail_selected)
    events = []
    with pytest.raises(SynthesisPreflightError) as caught:
        synthesis_stage.synthesize_project(
            project, cfg, request=request(project), on_event=events.append
        )

    error = caught.value
    assert error.code == "synthesis.preflight_failed"
    assert {issue["segment_id"] for issue in error.issues} == failed_ids
    assert all(issue["reason"] == "render.test_target_failure" for issue in error.issues)
    assert all(
        isinstance((source_path := issue.get("source_path")), str)
        and source_path.endswith("document/document.ssmd.md")
        for issue in error.issues
    )
    assert error.details["rendered_new_segments"] == 0
    assert error.details["synthesis_requests"] == 0
    assert error.details["audio_artifacts_written"] == 0
    assert set(lower_calls) == failed_ids
    assert adapter.open_calls == 0
    assert not list((project.state_root / "synthesis").rglob("*.wav"))
    assert events[-1].kind == "preflight_failed"


def test_preflighted_lowered_request_is_reused_during_render(tmp_path, monkeypatch) -> None:
    adapter = Adapter()
    cfg, project, _scope, plan = _two_segment_project(tmp_path, monkeypatch, adapter)
    original_lower = synthesis_stage.lower_segment
    lowered_by_id = {}
    lower_calls: list[str] = []

    def record_lower(plan_value, segment, selection, capabilities):
        lowered = original_lower(plan_value, segment, selection, capabilities)
        lower_calls.append(segment.id)
        lowered_by_id[segment.id] = lowered
        return lowered

    monkeypatch.setattr(synthesis_stage, "lower_segment", record_lower)
    import readio.rendering

    original_render = readio.rendering.render_atomic_request
    rendered_requests = []

    def assert_same_request(session, speech_request, **kwargs):
        rendered_requests.append(speech_request)
        assert speech_request is lowered_by_id[speech_request.id].request
        return original_render(session, speech_request, **kwargs)

    monkeypatch.setattr(readio.rendering, "render_atomic_request", assert_same_request)
    synthesis_stage.synthesize_project(project, cfg, request=request(project))

    assert set(lower_calls) == {segment.id for segment in plan.segments}
    assert len(lower_calls) == len(plan.segments)
    assert {item.id for item in rendered_requests} == {segment.id for segment in plan.segments}
    assert adapter.open_calls == 1


class MeasuringAdapter(Adapter):
    def __init__(self, maximum: int = 3):
        super().__init__()
        self.maximum = maximum
        self.synthesize_calls = 0
        self.measured_requests = []

    def capabilities(self):
        return replace(super().capabilities(), supports_request_measurement=True)

    def open(self, selection):
        inner_context = super().open(selection)

        @contextmanager
        def session_context():
            with inner_context as inner_session:

                class MeasuringSession:
                    def measure(_self, speech_request):
                        self.measured_requests.append(speech_request)
                        amount = len(speech_request.text)
                        return RequestMeasure(
                            fits=amount <= self.maximum,
                            amount=amount,
                            maximum=self.maximum,
                            unit="model_tokens",
                            source="fake.tokenizer",
                        )

                    def synthesize(_self, speech_request):
                        self.synthesize_calls += 1
                        return inner_session.synthesize(speech_request)

                yield MeasuringSession()

        return session_context()


def test_known_target_request_limits_aggregate_before_any_audio(tmp_path, monkeypatch) -> None:

    adapter = MeasuringAdapter(maximum=3)
    cfg, project, _scope, plan = _two_segment_project(tmp_path, monkeypatch, adapter)
    events = []
    with pytest.raises(SynthesisPreflightError) as caught:
        synthesis_stage.synthesize_project(
            project, cfg, request=request(project), on_event=events.append
        )

    issues = caught.value.issues
    assert len(issues) == len(plan.segments)
    assert {issue["reason"] for issue in issues} == {"synthesis.request_too_long"}
    for issue in issues:
        actual = issue.get("actual")
        maximum = issue.get("maximum")
        assert isinstance(actual, int)
        assert isinstance(maximum, int)
        assert actual > maximum == 3
    assert all(issue["measurement_source"] == "fake.tokenizer" for issue in issues)
    assert len(adapter.measured_requests) == len(plan.segments)
    assert adapter.open_calls == 1
    assert adapter.synthesize_calls == 0
    assert caught.value.details["rendered_new_segments"] == 0
    assert caught.value.details["synthesis_requests"] == 0
    assert caught.value.details["audio_artifacts_written"] == 0
    assert not list((project.state_root / "synthesis").rglob("*.wav"))
    assert events[-1].kind == "preflight_failed"


def test_public_api_forwards_synthesis_preflight_progress_and_issues(tmp_path, monkeypatch) -> None:
    adapter = MeasuringAdapter(maximum=3)
    cfg, project, _scope, plan = _two_segment_project(tmp_path, monkeypatch, adapter)
    app = Readio(config=cfg)
    events = []

    with pytest.raises(SynthesisPreflightError) as caught:
        app.projects.synthesize(
            project.root,
            SynthesisRequest(engine="fake", voice="fake-voice"),
            on_event=events.append,
        )

    synthesis_events = [
        event for event in events if event.kind == "progress" and event.stage == "synthesis"
    ]
    preflight_phases = [
        event for event in synthesis_events if event.details.get("phase") == "synthesis_preflight"
    ]
    assert any(
        event.progress_kind == "phase" and event.message == "Preflighting synthesis targets"
        for event in preflight_phases
    )
    assert len(
        [event for event in preflight_phases if event.progress_kind == "item.completed"]
    ) == len(plan.segments)
    failed = next(
        event for event in preflight_phases if event.message == "Synthesis preflight failed"
    )
    assert len(failed.details["issues"]) == len(plan.segments)
    assert failed.details["synthesis_requests"] == 0
    assert failed.details["audio_artifacts_written"] == 0
    assert len(caught.value.issues) == len(plan.segments)
    assert adapter.synthesize_calls == 0


def test_synth_cli_json_and_human_errors_include_preflight_diagnostics(
    tmp_path, monkeypatch, capsys
) -> None:
    from readio import cli

    adapter = MeasuringAdapter(maximum=3)
    cfg, project, _scope, plan = _two_segment_project(tmp_path, monkeypatch, adapter)
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: cfg)
    args = [
        "synth",
        str(project.root),
        "--engine",
        "fake",
        "--voice",
        "fake-voice",
        "--no-progress",
    ]

    with pytest.raises(SystemExit) as json_exit:
        cli.main([*args, "--json"])
    assert json_exit.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "synthesis.preflight_failed"
    assert len(payload["details"]["issues"]) == len(plan.segments)
    assert payload["rendered_new_segments"] == 0

    with pytest.raises(SystemExit) as human_exit:
        cli.main(args)
    assert human_exit.value.code == 2
    human_error = capsys.readouterr().err
    assert "fake-target" in human_error
    assert "synthesis.request_too_long" in human_error
    assert "known limit:" in human_error
    assert "No synthesis requests were sent" in human_error
    assert adapter.synthesize_calls == 0
    assert not list((project.state_root / "synthesis").rglob("*.wav"))
