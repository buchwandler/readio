from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from readio.api import (
    Document,
    ExecutionError,
    InputRequest,
    InvalidRequestError,
    OutputError,
    OutputRequest,
    PlanRequest,
    Readio,
    ReadioEvent,
    RenderSummary,
    SynthesisRequest,
)
from readio.audio import RenderProgress
from readio.config import ReadioConfig
from readio.execution import BoundedRenderResult


def _request(*, output: OutputRequest | None = None) -> PlanRequest:
    return PlanRequest(
        operation="render",
        input=InputRequest(
            document=Document(
                text="A public speech service test.",
                source_path=None,
                format="text",
            ),
        ),
        synthesis=SynthesisRequest(language="en-us", voice="af_sarah"),
        output=output or OutputRequest(),
    )


def _fake_execution(_resolved, sink, *, on_progress=None, on_phase=None):
    audio = np.zeros(32, dtype=np.float32)
    sink.write(audio, 22050)
    if on_phase is not None:
        on_phase("Composing WAV")
    if on_progress is not None:
        on_progress(RenderProgress(1, 1, len(audio), 22050))
    summary = RenderSummary(
        sample_rate=22050,
        sample_count=len(audio),
        channels=1,
    )
    composition = SimpleNamespace(
        sample_rate=22050,
        audio=audio,
        markers=(),
        spans=(),
        items=(),
    )
    return BoundedRenderResult(summary, SimpleNamespace(), composition)


class _Sink:
    def __init__(self) -> None:
        self.writes: list[tuple[np.ndarray, int]] = []
        self.closed = False

    def write(self, audio: np.ndarray, sample_rate: int) -> None:
        self.writes.append((audio, sample_rate))

    def close(self) -> None:
        self.closed = True


class _LineStream:
    def __init__(self) -> None:
        self.closed = False

    def __iter__(self):
        yield "First live line."
        yield "Second live line."

    def close(self) -> None:
        self.closed = True


class _OwnedPlaybackSink(_Sink):
    def __init__(self, _config) -> None:
        super().__init__()
        self.finished = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info) -> None:
        self.close()

    def finish(self) -> None:
        self.finished = True


def test_public_plan_is_serializable_and_does_not_create_output(tmp_path: Path) -> None:
    output = tmp_path / "not-created.wav"
    app = Readio(ReadioConfig())

    with patch("pykokoro.KokoroPipeline", side_effect=AssertionError("TTS loaded")):
        plan = app.speech.plan(
            _request(output=OutputRequest(requested_format="wav", requested_path=output))
        )

    assert plan.ok
    assert not output.exists()
    plan_dict = json.loads(json.dumps(plan.to_dict()))
    assert plan_dict["output"]["path"] == str(output)
    assert plan_dict["input"]["requested_format"] == "auto"


def test_render_to_sink_preserves_caller_ownership_and_composes_events(monkeypatch) -> None:
    from readio.api import speech

    monkeypatch.setattr(speech, "execute_bounded_v2", _fake_execution)
    operation_events: list[ReadioEvent] = []
    application_events: list[ReadioEvent] = []
    sink = _Sink()
    app = Readio(ReadioConfig(), on_event=application_events.append)

    result = app.speech.render_to_sink(
        _request(),
        sink,
        on_event=operation_events.append,
    )

    assert len(sink.writes) == 1
    assert not sink.closed
    assert result.output_path is None
    assert result.plan is not None
    assert [event.kind for event in operation_events] == [
        "operation.started",
        "stage.started",
        "progress",
        "operation.completed",
    ]
    assert [event.kind for event in application_events] == [
        "operation.started",
        "stage.started",
        "progress",
        "operation.completed",
    ]
    assert operation_events[1].stage == "composition"
    assert operation_events[2].audio_seconds == 32 / 22050


def test_render_writes_owned_file_and_manifest(monkeypatch, tmp_path: Path) -> None:
    from readio.api import speech

    monkeypatch.setattr(speech, "execute_bounded_v2", _fake_execution)
    output = tmp_path / "render.wav"
    result = Readio(ReadioConfig()).speech.render(
        _request(
            output=OutputRequest(
                mode="file",
                requested_format="wav",
                requested_path=output,
                force=True,
            )
        ),
        write_manifest=True,
    )

    assert result.output_path == output
    assert output.is_file()
    assert result.manifest_path == Path(f"{output}.readio.json")
    assert result.audio_format == "wav"
    assert result.manifest_schema == "readio.render-manifest.v2"
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "readio.render-manifest.v2"
    assert result.to_dict()["output_path"] == str(output)


def test_speak_owns_and_finishes_its_playback_sink(monkeypatch) -> None:
    from readio import audio
    from readio.api import speech

    monkeypatch.setattr(speech, "execute_bounded_v2", _fake_execution)
    owned_sink = _OwnedPlaybackSink(None)
    monkeypatch.setattr(audio, "PlaybackSink", lambda config: owned_sink)

    result = Readio(ReadioConfig()).speech.speak(_request())

    assert result.plan is not None
    assert owned_sink.writes
    assert owned_sink.finished
    assert owned_sink.closed


def test_live_render_consumes_but_does_not_close_inputs_or_sink(monkeypatch) -> None:
    from readio.api import speech

    seen: list[str] = []

    def fake_live(lines, _cfg, sink, *, unit, synthesis, on_progress):
        seen.extend(lines)
        sink.write(np.zeros(4, dtype=np.float32), 22050)
        on_progress(RenderProgress(2, None, 4, 22050))
        return RenderSummary(sample_rate=22050, sample_count=4, channels=1)

    monkeypatch.setattr(speech, "resolve_synthesis_request", lambda _cfg, request: request)
    monkeypatch.setattr(speech, "render_live_internal", fake_live)
    lines = _LineStream()
    sink = _Sink()
    events: list[ReadioEvent] = []

    result = Readio(ReadioConfig()).speech.render_live(
        lines,
        sink,
        synthesis=SynthesisRequest(language="en-us", voice="af_sarah"),
        on_event=events.append,
    )

    assert seen == ["First live line.", "Second live line."]
    assert not lines.closed
    assert not sink.closed
    assert result.plan is None
    assert events[-2].kind == "progress"
    assert events[-2].total is None
    assert events[-1].kind == "operation.completed"


def test_live_speak_owns_and_closes_its_playback_sink(monkeypatch) -> None:
    from readio import audio
    from readio.api import speech

    def fake_live(_lines, _cfg, sink, *, unit, synthesis, on_progress):
        sink.write(np.zeros(4, dtype=np.float32), 22050)
        return RenderSummary(sample_rate=22050, sample_count=4, channels=1)

    monkeypatch.setattr(speech, "resolve_synthesis_request", lambda _cfg, request: request)
    monkeypatch.setattr(speech, "render_live_internal", fake_live)
    owned_sink = _OwnedPlaybackSink(None)
    monkeypatch.setattr(audio, "PlaybackSink", lambda config: owned_sink)

    result = Readio(ReadioConfig()).speech.speak_live(iter(["live"]), synthesis=SynthesisRequest())

    assert result.plan is None
    assert owned_sink.finished
    assert owned_sink.closed


def test_event_handler_failure_is_translated_without_being_swallowed() -> None:
    app = Readio(ReadioConfig())
    sink = _Sink()

    def fail(_event: ReadioEvent) -> None:
        raise RuntimeError("callback failed")

    try:
        app.speech.render_to_sink(_request(), sink, on_event=fail)
    except ExecutionError as error:
        assert error.code == "event.handler_failed"
        assert isinstance(error.__cause__, RuntimeError)
    else:
        raise AssertionError("callback failure was swallowed")


def test_output_collision_has_a_stable_public_error(tmp_path: Path) -> None:
    output = tmp_path / "existing.wav"
    output.write_bytes(b"existing")
    app = Readio(ReadioConfig())

    try:
        app.speech.render(_request(output=OutputRequest(requested_path=output)))
    except OutputError as error:
        assert error.code == "output.exists"
    else:
        raise AssertionError("existing output was overwritten")


class _FileSink(_Sink):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info) -> None:
        self.close()

    def write(self, audio: np.ndarray, sample_rate: int) -> None:
        super().write(audio, sample_rate)
        self.path.write_bytes(b"live audio")


def _install_live_file_mocks(monkeypatch, *, supports_live: bool = True, fail: bool = False):
    from readio.api import speech

    sinks: list[_FileSink] = []
    monkeypatch.setattr(speech, "resolve_synthesis_request", lambda _cfg, request: request)
    monkeypatch.setattr(
        speech,
        "get_engine",
        lambda _engine: SimpleNamespace(
            capabilities=lambda: SimpleNamespace(supports_live=supports_live)
        ),
    )
    monkeypatch.setattr(speech, "ensure_audio_format_available", lambda _format: None)

    def create_sink(path: Path, _audio_format: str) -> _FileSink:
        sink = _FileSink(path)
        sinks.append(sink)
        return sink

    def render_live(lines, _cfg, sink, *, unit, synthesis, on_progress):
        tuple(lines)
        sink.write(np.zeros(4, dtype=np.float32), 22050)
        if fail:
            raise RuntimeError("live render failed")
        return RenderSummary(sample_rate=22050, sample_count=4, channels=1)

    monkeypatch.setattr(speech, "create_audio_sink", create_sink)
    monkeypatch.setattr(speech, "render_live_internal", render_live)
    return sinks


def test_render_live_to_file_creates_requested_output_and_owns_resources(
    monkeypatch, tmp_path: Path
) -> None:
    output = tmp_path / "live.wav"
    lines = _LineStream()
    sinks = _install_live_file_mocks(monkeypatch)

    result = Readio(ReadioConfig()).speech.render_live_to_file(
        lines, OutputRequest(requested_path=output)
    )

    assert output.read_bytes() == b"live audio"
    assert result.output_path == output
    assert result.audio_format == "wav"
    assert not lines.closed
    assert sinks[0].closed


def test_render_live_to_file_infers_format_from_suffix(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "live.ogg"
    _install_live_file_mocks(monkeypatch)

    result = Readio(ReadioConfig()).speech.render_live_to_file(
        iter(["live"]), OutputRequest(requested_path=output)
    )

    assert result.output_path == output
    assert result.audio_format == "ogg"


def test_render_live_to_file_uses_configured_default_path(monkeypatch, tmp_path: Path) -> None:
    from readio.config import PathSettings

    _install_live_file_mocks(monkeypatch)
    config = ReadioConfig(paths=PathSettings(output=tmp_path))

    result = Readio(config).speech.render_live_to_file(iter(["live"]), OutputRequest())

    assert result.output_path is not None
    assert result.output_path.parent == tmp_path
    assert result.output_path.suffix == ".wav"
    assert result.output_path.is_file()


def test_render_live_to_file_rejects_format_suffix_conflict(monkeypatch, tmp_path: Path) -> None:

    _install_live_file_mocks(monkeypatch)
    with pytest.raises(InvalidRequestError, match="conflicts") as error:
        Readio(ReadioConfig()).speech.render_live_to_file(
            iter(["live"]),
            OutputRequest(requested_format="mp3", requested_path=tmp_path / "live.wav"),
        )

    assert error.value.code == "request.output_format_invalid"


def test_render_live_to_file_rejects_playback_output() -> None:
    with pytest.raises(InvalidRequestError) as error:
        Readio(ReadioConfig()).speech.render_live_to_file(
            iter(["live"]), OutputRequest(mode="playback")
        )

    assert error.value.code == "request.file_output_required"


def test_render_live_to_file_reports_unavailable_encoder(monkeypatch, tmp_path: Path) -> None:
    from readio.api import speech

    _install_live_file_mocks(monkeypatch)
    monkeypatch.setattr(
        speech,
        "ensure_audio_format_available",
        lambda _format: (_ for _ in ()).throw(RuntimeError("encoder unavailable")),
    )

    with pytest.raises(OutputError, match="encoder unavailable") as error:
        Readio(ReadioConfig()).speech.render_live_to_file(
            iter(["live"]), OutputRequest(requested_path=tmp_path / "live.wav")
        )

    assert error.value.code == "output.encoder_unavailable"


def test_render_live_to_file_rejects_existing_output_without_force(
    monkeypatch, tmp_path: Path
) -> None:
    output = tmp_path / "live.wav"
    output.write_bytes(b"original")
    _install_live_file_mocks(monkeypatch)

    with pytest.raises(OutputError, match="already exists") as error:
        Readio(ReadioConfig()).speech.render_live_to_file(
            iter(["live"]), OutputRequest(requested_path=output)
        )

    assert error.value.code == "output.exists"
    assert output.read_bytes() == b"original"


def test_render_live_to_file_force_atomically_replaces_output(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "live.wav"
    output.write_bytes(b"original")
    _install_live_file_mocks(monkeypatch)

    result = Readio(ReadioConfig()).speech.render_live_to_file(
        iter(["live"]), OutputRequest(requested_path=output, force=True)
    )

    assert result.output_path == output
    assert output.read_bytes() == b"live audio"
    assert not tuple(tmp_path.glob(".live.*.wav"))


def test_render_live_to_file_failure_keeps_final_output_unchanged(
    monkeypatch, tmp_path: Path
) -> None:
    output = tmp_path / "live.wav"
    output.write_bytes(b"original")
    sinks = _install_live_file_mocks(monkeypatch, fail=True)

    with pytest.raises(ExecutionError, match="live render failed"):
        Readio(ReadioConfig()).speech.render_live_to_file(
            iter(["live"]), OutputRequest(requested_path=output, force=True)
        )

    assert output.read_bytes() == b"original"
    assert not tuple(tmp_path.glob(".live.*.wav"))
    assert sinks[0].closed


def test_live_unsupported_engine_is_rejected_before_synthesis(monkeypatch) -> None:
    from readio.api import speech

    rendered = False

    def render_live(*_args, **_kwargs):
        nonlocal rendered
        rendered = True

    _install_live_file_mocks(monkeypatch, supports_live=False)
    monkeypatch.setattr(speech, "render_live_internal", render_live)

    with pytest.raises(InvalidRequestError) as error:
        Readio(ReadioConfig()).speech.render_live(
            iter(["live"]), _Sink(), synthesis=SynthesisRequest(engine="piper")
        )

    assert error.value.code == "speech.live_unsupported"
    assert not rendered
