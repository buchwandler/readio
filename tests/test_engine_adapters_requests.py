from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
import pykokoro
from project_support import assert_neutral_session_contract

from readio.engines.base import PronunciationSpan, SpeechRequest, SpeechToken
from readio.engines.pipersynth import PiperSynthEngineAdapter, PiperSynthEngineSession
from readio.engines.pykokoro import PyKokoroEngineAdapter, PyKokoroEngineSession
from readio.engines.selection import EngineRequest


class _NativeValue:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


def test_pykokoro_adapter_resolves_default_model_separately_from_voice() -> None:
    selection, diagnostics = PyKokoroEngineAdapter().resolve(
        EngineRequest(engine="pykokoro", language="en-us", voice="af_heart")
    )

    assert not diagnostics
    assert selection.target_id == "v1.0"
    assert selection.voice == "af_heart"


def test_pykokoro_session_converts_request_and_result(monkeypatch) -> None:
    monkeypatch.setattr(pykokoro, "SynthesisSegment", _NativeValue)
    monkeypatch.setattr(pykokoro, "PronunciationOverride", _NativeValue)
    monkeypatch.setattr(pykokoro, "LinguisticToken", _NativeValue)
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
        def synthesize(self, segment):
            self.segment = segment
            return native_result

    synthesizer = _Synthesizer()
    request = SpeechRequest(
        id="segment-1",
        text="Hello",
        language="en-us",
        voice="af_heart",
        pronunciation_overrides=(PronunciationSpan(0, 5, "həˈloʊ", alphabet="ipa"),),
        tokens=(SpeechToken(0, 5, "Hello", pos="INTJ"),),
    )

    rendered = assert_neutral_session_contract(PyKokoroEngineSession(synthesizer), request)

    assert synthesizer.segment.text == "Hello"
    assert synthesizer.segment.pronunciation_overrides[0].phonemes == "həˈloʊ"
    assert synthesizer.segment.annotations[0].pos == "INTJ"
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


def test_piper_session_uses_published_text_request_api() -> None:
    @dataclass(frozen=True)
    class _Config:
        speaker_id: int | None = None

    chunks = (
        SimpleNamespace(
            audio_float_array=np.array([0.2], dtype=np.float32),
            sample_rate=22050,
            phonemes=("h",),
            phoneme_ids=(1,),
            warnings=(),
        ),
        SimpleNamespace(
            audio_float_array=np.array([-0.2], dtype=np.float32),
            sample_rate=22050,
            phonemes=("i",),
            phoneme_ids=(2,),
            warnings=("chunk warning",),
        ),
    )

    class _Voice:
        config = SimpleNamespace(sample_rate=22050)

        def resolve_speaker_id(self, speaker):
            self.speaker = speaker
            return 0

        def synthesize(self, text, syn_config):
            self.text = text
            self.config_used = syn_config
            return iter(chunks)

    voice = _Voice()
    request = SpeechRequest(
        id="segment-2",
        text="Hi",
        language="en-us",
        voice="lessac",
        speaker="narrator",
    )

    rendered = assert_neutral_session_contract(PiperSynthEngineSession(voice, _Config()), request)

    assert voice.text == "Hi"
    assert voice.speaker == "narrator"
    assert voice.config_used.speaker_id == 0
    assert rendered.id == request.id
    assert rendered.sample_rate == 22050
    assert rendered.warnings == ("chunk warning",)
    assert rendered.metadata["phonemes"] == ("h", "i")
    np.testing.assert_array_equal(rendered.audio, np.array([0.2, -0.2], dtype=np.float32))


def test_piper_release_capabilities_do_not_claim_token_or_pronunciation_support() -> None:
    capabilities = PiperSynthEngineAdapter().capabilities()

    assert capabilities.supports_speakers
    assert not capabilities.supports_linguistic_tokens
    assert not capabilities.supports_pronunciation_overrides


def test_piper_api_compatibility_checks_published_voice_signature(monkeypatch) -> None:
    import pipersynth

    class _PublishedVoice:
        def synthesize(self, text, syn_config=None):
            return iter(())

    monkeypatch.setattr(pipersynth, "PiperVoice", _PublishedVoice)
    monkeypatch.setattr(pipersynth, "SynthesisConfig", object, raising=False)

    assert PiperSynthEngineAdapter().compatible_api()
