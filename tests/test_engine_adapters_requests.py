from __future__ import annotations

import sys
from dataclasses import dataclass
from types import ModuleType, SimpleNamespace

import numpy as np
from project_support import assert_neutral_session_contract

from readio.engines.base import EngineSelection, PronunciationSpan, SpeechRequest, SpeechToken
from readio.engines.pipersynth import PiperSynthEngineAdapter, PiperSynthEngineSession
from readio.engines.pykokoro import PyKokoroEngineAdapter, PyKokoroEngineSession
from readio.engines.selection import EngineRequest


def _mock_pipersynth():
    class NativeObject:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    module = ModuleType("pipersynth")
    for name in (
        "SynthesisRequest",
        "SynthesisResult",
        "SynthesisConfig",
        "LinguisticToken",
        "PronunciationOverride",
        "VoiceLevelConfig",
        "SynthesisInputTooLongError",
    ):
        setattr(module, name, NativeObject)
    module.PiperVoice = NativeObject
    return module


def test_pykokoro_adapter_resolves_default_model_separately_from_voice() -> None:
    selection, diagnostics = PyKokoroEngineAdapter().resolve(
        EngineRequest(engine="pykokoro", language="en-us", voice="af_heart")
    )

    assert not diagnostics
    assert selection.target_id == "v1.0"
    assert selection.voice == "af_heart"


def test_pykokoro_open_maps_common_speed_and_voice_level_without_splitting(
    monkeypatch,
):
    import pykokoro

    class _Config:
        def __init__(self, **values):
            self.__dict__.update(values)

    class _Synthesizer:
        def __init__(self, config):
            self.config = config
            self.closed = False

        def close(self):
            self.closed = True

    monkeypatch.setattr(pykokoro, "GenerationConfig", _Config)
    monkeypatch.setattr(pykokoro, "TokenizerConfig", _Config)
    monkeypatch.setattr(pykokoro, "SynthesisConfig", _Config)
    monkeypatch.setattr(pykokoro, "VoiceLevelConfig", _Config)
    created = []

    def open_synthesizer(config):
        synthesizer = _Synthesizer(config)
        created.append(synthesizer)
        return synthesizer

    monkeypatch.setattr(pykokoro, "KokoroSynthesizer", open_synthesizer)
    selection = EngineSelection(
        engine="pykokoro",
        target_id="v1.0",
        language="en-us",
        voice="af_heart",
        options={"speed": 1.25, "voice_level": "calibrated"},
    )

    with PyKokoroEngineAdapter().open(selection):
        pass

    config = created[0].config
    assert config.generation.speed == 1.25
    assert config.voice_level.mode == "calibrated"
    assert config.long_text_split == "none"
    assert config.long_text_use_spacy is False
    assert created[0].closed


def test_pykokoro_session_converts_request_and_result(monkeypatch) -> None:
    native_result = SimpleNamespace(
        id="segment-1",
        audio=np.array([0.1, -0.1], dtype=np.float32),
        sample_rate=24000,
        word_timings=(),
        diagnostics=(),
        language="en-us",
        voice="af_heart",
        phonemes="həˈloʊ",
        token_ids=(),
        trace=None,
    )

    class _Synthesizer:
        def synthesize(self, request):
            self.request = request
            return native_result

    synthesizer = _Synthesizer()
    request = SpeechRequest(
        id="segment-1",
        text="Hello",
        language="en-us",
        voice="af_heart",
        pronunciation_overrides=(PronunciationSpan(0, 5, "həˈloʊ", alphabet="ipa"),),
        tokens=(
            SpeechToken(
                0,
                5,
                "Hello",
                pos="INTJ",
                morph="Number=Sing",
            ),
        ),
    )

    rendered = assert_neutral_session_contract(PyKokoroEngineSession(synthesizer), request)

    assert synthesizer.request.text == "Hello"
    assert synthesizer.request.pronunciation_overrides[0].phonemes == "həˈloʊ"
    assert synthesizer.request.tokens[0].pos == "INTJ"
    assert synthesizer.request.tokens[0].morph == "Number=Sing"
    assert rendered.id == request.id
    assert rendered.sample_rate == 24000
    np.testing.assert_array_equal(rendered.audio, native_result.audio)


def test_piper_adapter_maps_request_rate_to_native_length_scale() -> None:
    selection, diagnostics = PiperSynthEngineAdapter().resolve(
        EngineRequest(
            engine="piper",
            target_id="en_US-lessac-medium",
            language="en-us",
            voice="lessac",
            options={"speed": 2.0},
        )
    )

    assert not diagnostics
    assert selection.options["length_scale"] == 0.5


def test_piper_session_uses_one_published_request_api(monkeypatch) -> None:
    pipersynth = _mock_pipersynth()
    monkeypatch.setitem(sys.modules, "pipersynth", pipersynth)

    @dataclass(frozen=True)
    class _Config:
        speaker_id: int | None = None

    native_result = SimpleNamespace(
        id="segment-2",
        audio=np.array([0.2, -0.2], dtype=np.float32),
        sample_rate=22050,
        warnings=("native warning",),
        metadata={"phonemes": ("h", "i")},
    )

    class _Voice:
        def synthesize(self, request, *, config):
            self.request = request
            self.config_used = config
            return native_result

    voice = _Voice()
    request = SpeechRequest(
        id="segment-2",
        text="Hi",
        language="en-us",
        voice="lessac",
        speaker="narrator",
        tokens=(SpeechToken(0, 2, "Hi", pos="INTJ", morph="Number=Sing"),),
        pronunciation_overrides=(PronunciationSpan(0, 2, "haɪ", alphabet="ipa"),),
    )

    rendered = assert_neutral_session_contract(PiperSynthEngineSession(voice, _Config()), request)

    assert isinstance(voice.request, pipersynth.SynthesisRequest)
    assert voice.request.text == "Hi"
    assert voice.request.speaker == "narrator"
    assert voice.request.tokens[0].morph == "Number=Sing"
    assert voice.request.pronunciation_overrides[0].phonemes == "haɪ"
    assert rendered.id == request.id
    assert rendered.sample_rate == 22050
    assert rendered.warnings == ("native warning",)
    assert rendered.metadata["phonemes"] == ("h", "i")
    assert voice.config_used.speaker_id is None
    np.testing.assert_array_equal(rendered.audio, np.array([0.2, -0.2], dtype=np.float32))


def test_piper_release_capabilities_claim_request_context_support() -> None:
    capabilities = PiperSynthEngineAdapter().capabilities()

    assert capabilities.supports_speakers
    assert capabilities.supports_linguistic_tokens
    assert capabilities.supports_pronunciation_overrides
    assert capabilities.supports_voice_level_calibration


def test_piper_api_compatibility_checks_published_voice_signature(monkeypatch) -> None:
    pipersynth = _mock_pipersynth()
    monkeypatch.setitem(sys.modules, "pipersynth", pipersynth)

    class _PublishedVoice:
        def synthesize(self, request, *, config=None):
            return None

    monkeypatch.setattr(pipersynth, "PiperVoice", _PublishedVoice)

    assert PiperSynthEngineAdapter().compatible_api()


def test_piper_api_compatibility_rejects_legacy_text_signature(monkeypatch) -> None:
    pipersynth = _mock_pipersynth()
    monkeypatch.setitem(sys.modules, "pipersynth", pipersynth)

    class _LegacyVoice:
        def synthesize(self, text, syn_config=None):
            return iter(())

    monkeypatch.setattr(pipersynth, "PiperVoice", _LegacyVoice)

    assert not PiperSynthEngineAdapter().compatible_api()
