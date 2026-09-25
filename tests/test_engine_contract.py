from __future__ import annotations

from pathlib import Path

from readio.engines.base import (
    EngineAdapter,
    EngineCapabilities,
    EngineSession,
    PronunciationSpan,
    RenderedSpeech,
    SpeechRequest,
    SpeechToken,
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
