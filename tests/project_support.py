from __future__ import annotations

from contextlib import contextmanager

import numpy as np

from readio.engines.base import EngineCapabilities, EngineSelection, RenderedSpeech
from readio.plan import InputRequest, OutputRequest, PlanRequest, SynthesisRequest


class Session:
    def synthesize(self, request):
        return RenderedSpeech(
            id=request.id,
            audio=np.full(160, 0.1, dtype=np.float32),
            sample_rate=24000,
        )


class Adapter:
    id = "fake"

    def __init__(self):
        self.open_calls = 0

    def version(self):
        return "fake-1"

    def capabilities(self):
        return EngineCapabilities(
            id=self.id,
            voice_binding_namespace=self.id,
            supports_named_voices=True,
        )

    def resolve(self, request):
        return (
            EngineSelection(
                engine=self.id,
                target_id=request.target_id or "fake-target",
                language=request.language or "en-us",
                voice=request.voice,
                options=dict(request.options),
            ),
            (),
        )

    def canonical_synthesis_identity(self, selection):
        return {
            "engine": self.id,
            "target_id": selection.target_id,
            "voice": selection.voice,
        }

    def open(self, selection):
        self.open_calls += 1

        @contextmanager
        def session():
            yield Session()

        return session()


def request(project):
    return PlanRequest(
        "render",
        InputRequest(project.document()),
        SynthesisRequest(engine="fake", voice="fake-voice"),
        OutputRequest(mode="file", requested_format="wav", force=True),
    )


def assert_neutral_session_contract(session, request):
    result = session.synthesize(request)
    assert isinstance(result, RenderedSpeech)
    assert result.id == request.id
    assert result.sample_rate > 0
    audio = np.asarray(result.audio)
    assert audio.ndim == 1 and audio.dtype == np.float32
    assert np.isfinite(audio).all()
    return result
