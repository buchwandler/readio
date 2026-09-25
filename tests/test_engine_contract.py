from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from readio.api import (
    EmptySpeechTextError,
    EngineBackendError,
    EngineSynthesisError,
    InvalidEngineLanguageError,
    InvalidEngineModelError,
    InvalidEngineOptionError,
    InvalidEngineSpeakerError,
    InvalidEngineVoiceError,
    InvalidSpeechRequestError,
    RequestMeasure,
    SpeechRequestTooLongError,
    UnsupportedSynthesisFeatureError,
    translate_exception,
)
from readio.engines.base import (
    EngineAdapter,
    EngineCapabilities,
    EngineSession,
    PronunciationSpan,
    RenderedSpeech,
    SpeechRequest,
    SpeechToken,
    SpeechWordTiming,
    validate_rendered_speech,
)


def test_engine_session_is_request_centric() -> None:
    assert hasattr(EngineSession, "synthesize")
    assert not hasattr(EngineSession, "prepare_plan")
    assert not hasattr(EngineSession, "prepare_segments")
    assert not hasattr(EngineSession, "to_audio_job")
    assert not hasattr(EngineAdapter, "planner_config")


def test_neutral_request_and_result_types() -> None:
    request = SpeechRequest(
        id="segment-1",
        text="Hello.",
        language="en-us",
        pronunciation_overrides=(PronunciationSpan(0, 5, phonemes="həˈloʊ", alphabet="ipa"),),
        tokens=(SpeechToken(0, 5, "Hello"),),
    )
    result = RenderedSpeech("segment-1", [0.0, 0.1], 24000)

    assert request.pronunciation_overrides[0].start == 0
    assert request.tokens[0].text == "Hello"
    assert result.id == request.id
    assert result.sample_rate == 24000


def test_capabilities_describe_synthesis_semantics() -> None:
    capabilities = EngineCapabilities(
        id="test",
        voice_binding_namespace="test",
        supports_named_voices=True,
        supports_pronunciation_overrides=True,
        pronunciation_alphabets=frozenset({"ipa"}),
        supports_linguistic_tokens=True,
    )

    assert capabilities.voice_binding_namespace == "test"
    assert capabilities.supports_pronunciation_overrides
    assert capabilities.supports_linguistic_tokens
    assert not hasattr(capabilities, "supports_prepared_units")
    assert not hasattr(capabilities, "supports_audio_job")


def test_request_measure_and_capability_contract() -> None:
    measure = RequestMeasure(
        fits=False,
        amount=197,
        maximum=192,
        unit="model_tokens",
        source="test.frontend",
    )
    capabilities = EngineCapabilities(
        id="test",
        supports_voice_level_calibration=True,
        supports_request_measurement=True,
    )

    assert measure.fits is False
    assert measure.amount == 197
    assert capabilities.supports_voice_level_calibration
    assert capabilities.supports_request_measurement
    assert hasattr(EngineSession, "measure")


def test_rendered_speech_validation_normalizes_float_audio() -> None:
    request = SpeechRequest(id="segment-1", text="Hi", language="en", voice="voice-1")
    result = RenderedSpeech(
        id="segment-1",
        audio=[0, 0.5],
        sample_rate=np.int64(24000),
        word_timings=(SpeechWordTiming("Hi", 0, 2, 0, 2),),
    )

    checked = validate_rendered_speech(
        request, result, engine="piper", engine_version="0.2.0", target_id="piper-en"
    )

    assert checked is result
    assert checked.audio.dtype == np.float32
    assert checked.sample_rate == 24000
    assert checked.word_timings[0].char_end == 2


@pytest.mark.parametrize(
    "result",
    (
        RenderedSpeech("wrong-id", [0.0], 24000),
        RenderedSpeech("segment-1", [], 24000),
        RenderedSpeech("segment-1", [0.0], 0),
        RenderedSpeech("segment-1", [float("nan")], 24000),
        RenderedSpeech("segment-1", [[0.0, 0.1]], 24000),
        RenderedSpeech(
            "segment-1",
            [0.0],
            24000,
            word_timings=(SpeechWordTiming("Hi", -1, 1, 0, 1),),
        ),
        RenderedSpeech(
            "segment-1",
            [0.0],
            24000,
            word_timings=(SpeechWordTiming("Hi", 0, 2, 0, 2),),
        ),
        RenderedSpeech(
            "segment-1",
            [0.0, 0.0],
            24000,
            word_timings=(
                SpeechWordTiming("Hi", 1, 2, 0, 1),
                SpeechWordTiming("there", 0, 1, 1, 2),
            ),
        ),
    ),
)
def test_invalid_rendered_speech_raises_contextual_backend_error(result) -> None:
    request = SpeechRequest(id="segment-1", text="Hi", language="en", voice="voice-1")

    with pytest.raises(EngineBackendError) as exc_info:
        validate_rendered_speech(request, result, engine="piper", target_id="piper-en")

    assert exc_info.value.code == "synthesis.backend_error"
    assert exc_info.value.engine == "piper"
    assert exc_info.value.target_id == "piper-en"
    assert exc_info.value.request_id == "segment-1"
    assert exc_info.value.details["language"] == "en"


def test_engine_synthesis_errors_are_stable_public_readio_errors() -> None:
    errors = (
        EmptySpeechTextError,
        InvalidEngineModelError,
        InvalidEngineVoiceError,
        InvalidEngineSpeakerError,
        InvalidEngineLanguageError,
        InvalidEngineOptionError,
        InvalidSpeechRequestError,
        SpeechRequestTooLongError,
        UnsupportedSynthesisFeatureError,
        EngineBackendError,
    )
    assert all(issubclass(error, EngineSynthesisError) for error in errors)

    error = SpeechRequestTooLongError(
        "request too long",
        engine="pocket",
        request_id="segment-1",
        amount=197,
        maximum=192,
        unit="model_tokens",
        source="pocketsynth.runtime",
    )
    assert error.code == "synthesis.request_too_long"
    assert error.details["amount"] == 197
    assert error.details["maximum"] == 192
    assert translate_exception(error) is error


def test_engine_adapters_do_not_depend_on_semantic_or_composition_types() -> None:
    engine_dir = Path(__file__).parents[1] / "readio" / "engines"
    forbidden = (
        "UtterancePlan",
        "AudioJob",
        "KokoroPipeline",
        "PipelineConfig",
        "SSMDRenderConfig",
        "PiperPipeline",
        "pykokoro.planning",
        "pipersynth.planning",
        "prepare_plan",
        "to_audio_job",
    )

    for path in engine_dir.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for name in forbidden:
            assert name not in source, (
                f"{name} leaked into {path.relative_to(engine_dir.parent.parent)}"
            )


def test_legacy_backend_architecture_is_removed() -> None:
    readio_dir = Path(__file__).parents[1] / "readio"
    assert not (readio_dir / "backends").exists()

    for path in readio_dir.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "from .backends" not in source, path
        assert "from ..backends" not in source, path
