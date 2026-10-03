from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np
import pytest
from audiocompose import Tempo

from readio.audio import RenderProgress, RenderSummary
from readio.config import ReaderSettings, ReadioConfig
from readio.document import document_from_text
from readio.engines.base import EngineCapabilities, EngineSelection, RenderedSpeech
from readio.engines.registry import _registry
from readio.engines.selection import EngineRequest
from readio.errors import RenderError
from readio.execution import execute_playback_v2, execute_render_v2
from readio.plan import (
    CompositionOptions,
    InputRequest,
    OutputRequest,
    PlanRequest,
    SynthesisRequest,
    resolve_execution_v2,
)


class _Session:
    def __init__(self) -> None:
        self.requests = []

    def synthesize(self, request):
        self.requests.append(request)
        return RenderedSpeech(
            id=request.id,
            audio=np.ones(8, dtype=np.float32),
            sample_rate=24000,
        )


class _Adapter:
    id = "request-fixture"

    def __init__(self) -> None:
        self.session = _Session()
        self.open_selections = []

    def version(self):
        return "fixture-1"

    def capabilities(self):
        return EngineCapabilities(id=self.id, supports_named_voices=True)

    def discover(self, request):
        return ()

    def resolve(self, request: EngineRequest):
        return (
            EngineSelection(
                engine=self.id,
                target_id=request.target_id or "fixture-target",
                language=request.language or "en-us",
                voice=request.voice,
                options=dict(request.options),
            ),
            (),
        )

    def canonical_synthesis_identity(self, selection):
        return {"engine": self.id, "target": selection.target_id}

    @contextmanager
    def open(self, selection):
        self.open_selections.append(selection)
        yield self.session


class _Sink:
    def __init__(self) -> None:
        self.writes = []

    def write(self, audio, sample_rate):
        self.writes.append((np.asarray(audio), sample_rate))


def test_bounded_execution_lowers_segments_to_engine_requests(monkeypatch) -> None:
    adapter = _Adapter()
    monkeypatch.setitem(_registry._adapters, adapter.id, adapter)
    config = ReadioConfig(reader=ReaderSettings(engine=adapter.id, voice="fixture-voice"))
    request = PlanRequest(
        operation="render",
        input=InputRequest(document=document_from_text("A request-centric render.")),
        synthesis=SynthesisRequest(engine=adapter.id, voice="fixture-voice", speed=1.5),
        output=OutputRequest(mode="playback"),
        composition=CompositionOptions(sample_rate=16000, clip_policy="warn"),
    )

    captured = {}

    class _RecordingComposer:
        def compose(self, job, on_progress=None):
            captured["job"] = job
            return SimpleNamespace(
                audio=np.zeros(16, dtype=np.float32),
                sample_rate=job.output.sample_rate,
                items=(),
                markers=(),
                spans=(),
                loudness=None,
            )

    monkeypatch.setattr("readio.stages.composition.Composer", _RecordingComposer)
    resolved = resolve_execution_v2(config, request)
    sink = _Sink()
    result = execute_render_v2(resolved, sink)

    assert resolved.plan.ok
    assert resolved.plan.to_dict()["composition"]["sample_rate"] == 16000
    assert adapter.session.requests
    assert adapter.session.requests[0].text
    job = captured["job"]
    assert job.items[0].metadata["segment_id"] == adapter.session.requests[0].id
    assert sink.writes[0][1] == 16000
    assert result.summary.sample_rate == 16000
    assert job.output.sample_rate == 16000
    assert job.output.clip_policy == "warn"
    assert job.output.loudness.target_lufs == -16.0
    assert job.output.loudness.true_peak_ceiling_dbtp == -1.0
    assert adapter.open_selections[0].options["speed"] == 1.5
    assert not any(isinstance(item, Tempo) for item in job.items[0].operations)
    assert isinstance(result.summary, RenderSummary)


def test_playback_composes_and_releases_each_mixed_rate_segment_before_next_synthesis(
    monkeypatch,
) -> None:
    adapter = _Adapter()
    monkeypatch.setitem(_registry._adapters, adapter.id, adapter)
    events: list[tuple[str, str | int]] = []

    class ReleasableSpeech(RenderedSpeech):
        __slots__ = ("release_callback",)

        def release_audio(self):
            self.release_callback()

    def synthesize(request):
        events.append(("synthesize", request.text))
        sample_rate = 24000 if "First" in request.text else 22050
        rendered = ReleasableSpeech(
            id=request.id,
            audio=np.ones(64, dtype=np.float32),
            sample_rate=sample_rate,
        )
        rendered.release_callback = lambda text=request.text: events.append(("release", text))
        adapter.session.requests.append(request)
        return rendered

    monkeypatch.setattr(adapter.session, "synthesize", synthesize)
    from types import SimpleNamespace

    from readio import rendering

    monkeypatch.setattr(
        rendering,
        "render_atomic_request",
        lambda session, speech_request: SimpleNamespace(result=session.synthesize(speech_request)),
    )
    config = ReadioConfig(reader=ReaderSettings(engine=adapter.id, voice="fixture-voice"))
    request = PlanRequest(
        operation="render",
        input=InputRequest(document=document_from_text("First sentence.\n\nSecond sentence.")),
        synthesis=SynthesisRequest(engine=adapter.id, voice="fixture-voice"),
        output=OutputRequest(mode="playback"),
        composition=CompositionOptions(sample_rate=16000),
    )
    resolved = resolve_execution_v2(config, request)
    assert resolved.semantic is not None
    assert len(resolved.semantic.plan.segments) >= 2

    class OrderedSink(_Sink):
        def write(self, audio, sample_rate):
            events.append(("write", sample_rate))
            super().write(audio, sample_rate)

    sink = OrderedSink()
    progress: list[RenderProgress] = []
    result = execute_playback_v2(resolved, sink, on_progress=progress.append)

    event_kinds = [kind for kind, _value in events]
    assert event_kinds[:6] == [
        "synthesize",
        "write",
        "release",
        "synthesize",
        "write",
        "release",
    ]
    assert [sample_rate for _audio, sample_rate in sink.writes] == [16000] * len(sink.writes)
    assert len(sink.writes) == len(resolved.semantic.plan.segments)
    assert result.summary.sample_rate == 16000
    assert result.summary.sample_count == sum(len(audio) for audio, _rate in sink.writes)
    assert [item.completed_units for item in progress] == list(range(len(sink.writes) + 1))
    assert progress[0] == RenderProgress(0, len(resolved.semantic.plan.segments), 0, 16000)
    assert progress[-1].sample_count == result.summary.sample_count
    assert not hasattr(result, "composition")


def test_unrepresentable_pronunciation_fails_before_opening_engine(monkeypatch):
    adapter = _Adapter()
    monkeypatch.setitem(_registry._adapters, adapter.id, adapter)
    config = ReadioConfig(reader=ReaderSettings(engine=adapter.id, voice="fixture-voice"))
    request = PlanRequest(
        operation="render",
        input=InputRequest(
            document=document_from_text('[word]{ph="wɜːd" alphabet="ipa"}', input_format="ssmd"),
        ),
        synthesis=SynthesisRequest(engine=adapter.id, voice="fixture-voice"),
        output=OutputRequest(mode="playback"),
    )

    resolved = resolve_execution_v2(config, request)

    with pytest.raises(RenderError):
        execute_render_v2(resolved, _Sink())

    assert adapter.open_selections == []
