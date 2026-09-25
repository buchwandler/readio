from __future__ import annotations

from contextlib import contextmanager

import numpy as np
import pytest
from audiocompose import Tempo

from readio.audio import RenderSummary
from readio.config import ReaderSettings, ReadioConfig
from readio.document import document_from_text
from readio.engines.base import EngineCapabilities, EngineSelection, RenderedSpeech
from readio.engines.registry import _registry
from readio.engines.selection import EngineRequest
from readio.errors import RenderError
from readio.execution import execute_bounded_v2
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

    resolved = resolve_execution_v2(config, request)
    sink = _Sink()
    result = execute_bounded_v2(resolved, sink)

    assert resolved.plan.ok
    assert resolved.plan.to_dict()["composition"]["sample_rate"] == 16000
    assert adapter.session.requests
    assert adapter.session.requests[0].text
    assert result.audio_job.items[0].metadata["segment_id"] == adapter.session.requests[0].id
    assert sink.writes[0][1] == 16000
    assert result.summary.sample_rate == 16000
    assert result.audio_job.output.sample_rate == 16000
    assert result.audio_job.output.clip_policy == "warn"
    assert adapter.open_selections[0].options["speed"] == 1.5
    assert not any(isinstance(item, Tempo) for item in result.audio_job.items[0].operations)
    assert isinstance(result.summary, RenderSummary)


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
        execute_bounded_v2(resolved, _Sink())

    assert adapter.open_selections == []
