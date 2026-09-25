from __future__ import annotations

import sys
from types import ModuleType

from readio.engines.pykokoro import PyKokoroEngineAdapter

_REQUIRED_REQUEST_API = (
    "KokoroSynthesizer",
    "SynthesisConfig",
    "SynthesisRequest",
    "PronunciationOverride",
    "LinguisticToken",
    "VoiceLevelConfig",
    "SynthesisInputTooLongError",
    "ShortSentenceConfig",
)


def _module_with(*names: str) -> ModuleType:
    module = ModuleType("pykokoro")
    for name in names:
        setattr(module, name, object)
    return module


def test_pykokoro_adapter_rejects_the_legacy_pipeline_api(monkeypatch):
    legacy = _module_with("KokoroPipeline", "PipelineConfig", "GenerationConfig")
    monkeypatch.setitem(sys.modules, "pykokoro", legacy)

    assert not PyKokoroEngineAdapter().compatible_api()


def test_pykokoro_adapter_accepts_the_request_centric_api(monkeypatch):
    request_api = _module_with(*_REQUIRED_REQUEST_API)

    class _Synthesizer:
        def prepare(self, request):
            return None

        def synthesize(self, request):
            return None

    request_api.KokoroSynthesizer = _Synthesizer
    monkeypatch.setitem(sys.modules, "pykokoro", request_api)

    assert PyKokoroEngineAdapter().compatible_api()
