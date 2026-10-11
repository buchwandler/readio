from __future__ import annotations

from contextlib import contextmanager

import numpy as np
from voicerender import (
    EngineApiProbe,
    EngineCapabilities,
    EngineSelection,
    RenderedSpeech,
    RequestMeasure,
    SpeechWordTiming,
)


class FixtureSession:
    def __init__(self, adapter):
        self.adapter = adapter

    def measure(self, request):
        return RequestMeasure(
            fits=True,
            amount=len(request.text),
            maximum=len(request.text),
            unit="unknown",
            source="test-fixture",
        )

    def synthesize(self, request):
        self.adapter.requests.append(request)
        audio = np.full(2400, 0.08, dtype=np.float32)
        word = request.text.split()[0]
        start = request.text.index(word)
        return RenderedSpeech(
            id=request.id,
            audio=audio,
            sample_rate=24000,
            word_timings=(
                SpeechWordTiming(
                    text=word,
                    char_start=start,
                    char_end=start + len(word),
                    start_sample=100,
                    end_sample=2200,
                ),
            ),
        )


class FixtureAdapter:
    id = "readio-contract-fixture"
    package_name = "readio-contract-fixture"

    def __init__(self):
        self.requests = []
        self.opened_selections = []
        self.session_count = 0

    def capabilities(self):
        return EngineCapabilities(
            id=self.id,
            supports_live=True,
            supports_timestamps=True,
            supports_linguistic_tokens=True,
            supports_request_measurement=True,
        )

    def version(self):
        return "1.0"

    def probe_api(self):
        return EngineApiProbe(
            engine=self.id,
            package=self.package_name,
            compatible=True,
            status="ready",
            distribution_version="1.0",
            expected_api_version=1,
            api_version=1,
            contract_source="explicit",
        )

    def resolve(self, request):
        return (
            EngineSelection(
                engine=self.id,
                target_id=request.target_id or "fixture-model",
                language=request.language or "en-US",
                voice=request.voice,
                speaker=request.speaker,
                options=request.options,
            ),
            (),
        )

    def validate_selection(self, selection):
        assert selection.engine == self.id
        return ()

    def canonical_synthesis_identity(self, selection):
        return {
            "engine": self.id,
            "target_id": selection.target_id,
            "voice": selection.voice,
            "speaker": selection.speaker,
            "options": dict(selection.options),
        }

    @contextmanager
    def open(self, selection):
        self.opened_selections.append(selection)
        self.session_count += 1
        yield FixtureSession(self)
