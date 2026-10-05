from __future__ import annotations

import importlib.metadata
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

supertonicsynth = pytest.importorskip("supertonicsynth")

from readio.engines.base import EngineSelection, SpeechRequest, SpeechToken
from readio.engines.catalog import CatalogRequest
from readio.engines.selection import EngineRequest
from readio.engines.supertonicsynth import (
    SUPERTONIC_OPTION_NAMES,
    SupertonicSelectionError,
    SupertonicSynthEngineAdapter,
    SupertonicSynthEngineSession,
)
from readio.errors import (
    EngineBackendError,
    InvalidEngineLanguageError,
    InvalidEngineOptionError,
    InvalidEngineVoiceError,
    SpeechRequestTooLongError,
    UnsupportedSynthesisFeatureError,
)


def _model(
    *,
    model_id: str = "supertonic-3",
    aliases: tuple[str, ...] = ("supertonic-latest",),
    languages: tuple[str, ...] = ("en", "de", "na"),
    source_revision: str | None = "catalog-rev-1",
) -> Any:
    voices = (
        SimpleNamespace(
            id="F1",
            gender="female",
            language="en",
            locale="en-US",
            language_label="English",
        ),
        SimpleNamespace(
            id="M1",
            gender="male",
            language="de",
            locale="de-DE",
            language_label="Deutsch",
        ),
    )
    return SimpleNamespace(
        id=model_id,
        ref=f"supertonic:{model_id}",
        display_name="Supertonic 3",
        version="3",
        sample_rate=44_100,
        aliases=aliases,
        languages=languages,
        voices=voices,
        default_voice="M1",
        source_revision=source_revision,
        max_input_tokens=512,
        runtime_available=True,
        metadata={"catalog_field": "value"},
    )


def _install_catalog(
    monkeypatch: pytest.MonkeyPatch, model: Any | None = None
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def discover_models(**kwargs: Any) -> tuple[Any, ...]:
        calls.append(kwargs)
        return (_model() if model is None else model,)

    monkeypatch.setattr(supertonicsynth, "discover_models", discover_models)
    return calls


def _selection(**changes: Any) -> EngineSelection:
    values: dict[str, Any] = {
        "engine": "supertonic",
        "target_id": "supertonic-3",
        "language": "en-US",
        "voice": "F1",
        "metadata": {
            "source_revision": "catalog-rev-1",
            "backing_ref": "supertonic:supertonic-3",
        },
    }
    values.update(changes)
    return EngineSelection(**values)


def test_adapter_uses_released_public_api_without_opening_model() -> None:
    adapter = SupertonicSynthEngineAdapter()
    assert adapter.compatible_api()
    assert adapter.version() == importlib.metadata.version("supertonicsynth")
    assert callable(supertonicsynth.discover_models)
    assert callable(supertonicsynth.SupertonicRuntime.synthesize)


def test_capabilities_and_option_names_match_supported_contract() -> None:
    capabilities = SupertonicSynthEngineAdapter().capabilities()
    assert capabilities.id == "supertonic"
    assert capabilities.voice_binding_namespace == "supertonic"
    assert capabilities.voice_binding_scope == "target"
    assert capabilities.option_names == SUPERTONIC_OPTION_NAMES
    assert capabilities.supports_named_voices
    assert capabilities.supports_voice_level_calibration
    assert capabilities.supports_request_measurement
    assert not capabilities.supports_reference_voice
    assert not capabilities.supports_speakers
    assert not capabilities.supports_pronunciation_overrides
    assert not capabilities.supports_linguistic_tokens
    assert not capabilities.supports_whole_request_phonemes
    assert not capabilities.supports_qualities


def test_discovery_maps_metadata_and_forwards_language_offline_refresh(monkeypatch) -> None:
    calls = _install_catalog(monkeypatch)

    targets = SupertonicSynthEngineAdapter().discover(
        CatalogRequest(engine="supertonic", language="en-US", offline=True, refresh=True)
    )

    assert calls == [{"language": "en", "offline": True, "refresh": True, "cache_dir": None}]
    target = targets[0]
    assert target.engine == "supertonic"
    assert target.id == "supertonic-3"
    assert target.aliases == ("supertonic-latest",)
    assert target.sample_rate == 44_100
    assert target.languages == ("en", "de")
    assert target.voices == ("F1", "M1")
    assert target.default_voice == "M1"
    assert target.voice_details[0].gender == "female"
    assert target.voice_details[0].locale == "en-us"
    assert target.metadata["source_revision"] == "catalog-rev-1"
    assert "na" not in target.languages


def test_discovery_uses_metadata_api_only(monkeypatch) -> None:
    _install_catalog(monkeypatch)

    def fail_open(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("discovery must not open a synthesis runtime")

    monkeypatch.setattr(supertonicsynth.SupertonicRuntime, "from_pretrained", fail_open)
    targets = SupertonicSynthEngineAdapter().discover(CatalogRequest(engine="supertonic"))
    assert targets[0].id == "supertonic-3"


def test_discovery_does_not_publish_unknown_language_sentinel(monkeypatch) -> None:
    model = _model(languages=("na",))
    model.voices = (
        SimpleNamespace(id="UNK", gender=None, language="na", locale="na", language_label="na"),
    )
    _install_catalog(monkeypatch, model)
    target = SupertonicSynthEngineAdapter().discover(CatalogRequest(engine="supertonic"))[0]
    assert target.languages == ()
    assert target.voice_details[0].language == "unknown"


def test_resolve_uses_target_and_catalog_default_voice(monkeypatch) -> None:
    calls = _install_catalog(monkeypatch)

    selection, diagnostics = SupertonicSynthEngineAdapter().resolve(
        EngineRequest(engine="supertonic", language="en-US")
    )

    assert diagnostics == ()
    assert selection.target_id == "supertonic-3"
    assert selection.language == "en-us"
    assert selection.voice == "M1"
    assert selection.options["steps"] == supertonicsynth.GenerationConfig().steps
    assert selection.options["speed"] == supertonicsynth.GenerationConfig().speed
    assert calls[0]["language"] is None


def test_resolve_canonicalizes_model_alias_and_maps_speed_and_generation_options(
    monkeypatch,
) -> None:
    _install_catalog(monkeypatch)
    selection, _ = SupertonicSynthEngineAdapter().resolve(
        EngineRequest(
            engine="supertonic",
            target_id="supertonic-latest",
            language="de-DE",
            voice="F1",
            options={"speed": 1.15, "voice_level": "calibrated"},
            engine_options={"steps": 7, "seed": 42},
        )
    )
    assert selection.target_id == "supertonic-3"
    assert selection.voice == "F1"
    assert selection.options["steps"] == 7
    assert selection.options["speed"] == 1.15
    assert selection.options["seed"] == 42
    assert selection.options["voice_level"] == "calibrated"


def test_resolve_rejects_invalid_engine_options_and_generation_values(monkeypatch) -> None:
    _install_catalog(monkeypatch)
    adapter = SupertonicSynthEngineAdapter()
    with pytest.raises(SupertonicSelectionError, match="Unsupported SupertonicSynth option"):
        adapter.resolve(EngineRequest(engine="supertonic", engine_options={"max_chunk_length": 12}))
    with pytest.raises(SupertonicSelectionError, match="steps must be between") as error:
        adapter.resolve(EngineRequest(engine="supertonic", engine_options={"steps": 0}))
    assert error.value.diagnostic_code == "supertonic.invalid_option"


def test_validate_selection_reports_unsupported_language_and_voice(monkeypatch) -> None:
    _install_catalog(monkeypatch)
    selection = _selection(language="fr-FR", voice="F9")
    diagnostics = SupertonicSynthEngineAdapter().validate_selection(selection)
    assert [item.code for item in diagnostics] == [
        "supertonic.language_unsupported",
        "supertonic.voice_unavailable",
    ]


def test_measure_maps_public_capacity_result_once(monkeypatch) -> None:
    class Runtime:
        calls = 0

        def measure_request(self, request: Any) -> Any:
            self.calls += 1
            self.request = request
            return supertonicsynth.RequestMeasure(amount=10, maximum=12)

    runtime = Runtime()
    session = SupertonicSynthEngineSession(
        runtime,
        _selection(),
        supertonicsynth.GenerationConfig(),
        supertonicsynth.VoiceLevelConfig(),
    )

    measured = session.measure(SpeechRequest("segment-1", "A precise request.", "en-US"))

    assert runtime.calls == 1
    assert runtime.request.language == "en"
    assert measured.amount == 10
    assert measured.maximum == 12
    assert measured.fits is True
    assert measured.unit == "model_tokens"


def test_synthesis_forwards_one_exact_atomic_request_and_no_composition_options() -> None:
    class Runtime:
        calls = 0

        def synthesize(self, request: Any, *, voice: str, config: Any, voice_level: Any) -> Any:
            self.calls += 1
            self.arguments = request, voice, config, voice_level
            return supertonicsynth.AtomicSynthesisResult(
                id=request.id,
                audio=np.array([0.2, -0.2], dtype=np.float32),
                sample_rate=44_100,
                text=request.text,
                language=request.language,
                metadata={"voice_level": {"applied": False, "source": "off"}},
            )

    runtime = Runtime()
    generation = supertonicsynth.GenerationConfig(steps=7, speed=1.2, seed=42)
    voice_level = supertonicsynth.VoiceLevelConfig(mode="calibrated")
    session = SupertonicSynthEngineSession(runtime, _selection(), generation, voice_level)
    text = "Exact text. No adapter chunking, silence, normalization, or gain."

    rendered = session.synthesize(SpeechRequest("segment-1", text, "en-US"))

    assert runtime.calls == 1
    native, voice, actual_generation, actual_voice_level = runtime.arguments
    assert (native.id, native.text, native.language) == ("segment-1", text, "en")
    assert voice == "F1"
    assert actual_generation == generation
    assert actual_voice_level == voice_level
    np.testing.assert_allclose(rendered.audio, [0.2, -0.2])
    assert rendered.sample_rate == 44_100
    assert rendered.word_timings == ()
    assert rendered.metadata["engine_language"] == "en"


@pytest.mark.parametrize(
    "speech_request",
    [
        SpeechRequest("x", "text", "en", tokens=(SpeechToken(0, 4, "text"),)),
        SpeechRequest("x", "text", "en", whole_request_phonemes="t ɛ s t"),
        SpeechRequest("x", "text", "en", speaker="speaker-1"),
    ],
)
def test_synthesis_rejects_unsupported_readio_features(speech_request: SpeechRequest) -> None:
    session = SupertonicSynthEngineSession(
        SimpleNamespace(),
        _selection(),
        supertonicsynth.GenerationConfig(),
        supertonicsynth.VoiceLevelConfig(),
    )
    with pytest.raises(UnsupportedSynthesisFeatureError):
        session.synthesize(speech_request)


def test_synthesis_translates_too_long_and_backend_errors() -> None:
    too_long = supertonicsynth.SynthesisInputTooLongError(
        text_length=100,
        token_count=40,
        max_tokens=32,
        model_id="supertonic-3",
    )

    class Runtime:
        def synthesize(self, *_args: Any, **_kwargs: Any) -> None:
            raise too_long

    session = SupertonicSynthEngineSession(
        Runtime(),
        _selection(),
        supertonicsynth.GenerationConfig(),
        supertonicsynth.VoiceLevelConfig(),
    )
    with pytest.raises(SpeechRequestTooLongError) as error:
        session.synthesize(SpeechRequest("x", "text", "en"))
    assert error.value.amount == 40
    assert error.value.maximum == 32
    assert error.value.unit == "model_tokens"

    class BrokenRuntime:
        def synthesize(self, *_args: Any, **_kwargs: Any) -> None:
            raise supertonicsynth.ModelInferenceError("inference failed")

    broken = SupertonicSynthEngineSession(
        BrokenRuntime(),
        _selection(),
        supertonicsynth.GenerationConfig(),
        supertonicsynth.VoiceLevelConfig(),
    )
    with pytest.raises(EngineBackendError, match="inference failed"):
        broken.synthesize(SpeechRequest("x", "text", "en"))


def test_synthesis_translates_invalid_voice_and_language() -> None:
    class Runtime:
        def __init__(self, error: Exception) -> None:
            self.error = error

        def synthesize(self, *_args: Any, **_kwargs: Any) -> None:
            raise self.error

    voice_session = SupertonicSynthEngineSession(
        Runtime(supertonicsynth.InvalidVoiceStyleError("unknown voice")),
        _selection(),
        supertonicsynth.GenerationConfig(),
        supertonicsynth.VoiceLevelConfig(),
    )
    with pytest.raises(InvalidEngineVoiceError):
        voice_session.synthesize(SpeechRequest("x", "text", "en"))

    language_session = SupertonicSynthEngineSession(
        Runtime(supertonicsynth.InvalidLanguageError("unsupported language")),
        _selection(),
        supertonicsynth.GenerationConfig(),
        supertonicsynth.VoiceLevelConfig(),
    )
    with pytest.raises(InvalidEngineLanguageError):
        language_session.synthesize(SpeechRequest("x", "text", "fr"))


def test_canonical_identity_tracks_audio_inputs_but_not_display_metadata(monkeypatch) -> None:
    adapter = SupertonicSynthEngineAdapter()
    base = _selection()
    base_identity = adapter.canonical_synthesis_identity(base)
    display_only = replace(base, metadata={**base.metadata, "display_name": "New display"})
    assert adapter.canonical_synthesis_identity(display_only) == base_identity

    assert adapter.canonical_synthesis_identity(replace(base, voice="M1")) != base_identity
    assert adapter.canonical_synthesis_identity(replace(base, language="de-DE")) != base_identity
    assert (
        adapter.canonical_synthesis_identity(replace(base, options={"steps": 8})) != base_identity
    )
    assert (
        adapter.canonical_synthesis_identity(replace(base, options={"speed": 1.2})) != base_identity
    )
    assert (
        adapter.canonical_synthesis_identity(replace(base, options={"seed": 17})) != base_identity
    )
    assert (
        adapter.canonical_synthesis_identity(
            replace(base, metadata={**base.metadata, "source_revision": "catalog-rev-2"})
        )
        != base_identity
    )

    monkeypatch.setattr(
        supertonicsynth,
        "default_voice_calibration",
        lambda: SimpleNamespace(revision="calibration-rev-2"),
    )
    calibrated = replace(base, options={"voice_level": "calibrated"})
    identity = adapter.canonical_synthesis_identity(calibrated)
    assert identity["voice_level"]["catalog_revision"] == "calibration-rev-2"
    assert identity != adapter.canonical_synthesis_identity(
        replace(calibrated, options={"voice_level": "off"})
    )


def test_open_uses_public_runtime_and_closes_it(monkeypatch) -> None:
    calls: dict[str, Any] = {}

    class Runtime:
        bundle = SimpleNamespace(bundle_id="supertonic-3", source_revision="catalog-rev-1")

        def close(self) -> None:
            calls["closed"] = calls.get("closed", 0) + 1

    runtime = Runtime()

    class RuntimeAPI:
        @classmethod
        def from_pretrained(cls, ref: str, **options: Any) -> Runtime:
            calls["ref"] = ref
            calls["options"] = options
            return runtime

    monkeypatch.setattr(supertonicsynth, "SupertonicRuntime", RuntimeAPI)
    selection = _selection(
        options={"steps": 7, "speed": 1.1, "seed": 2, "voice_level": "off"},
        offline=True,
        refresh=True,
    )
    adapter = SupertonicSynthEngineAdapter()

    with adapter.open(selection) as session:
        assert isinstance(session, SupertonicSynthEngineSession)

    assert calls["ref"] == "supertonic-3"
    assert calls["options"]["offline"] is True
    assert calls["options"]["refresh_catalog"] is True
    assert calls["closed"] == 1


def test_open_rejects_bundle_revision_drift_and_closes_runtime(monkeypatch) -> None:
    class Runtime:
        bundle = SimpleNamespace(bundle_id="supertonic-3", source_revision="new-revision")
        closed = False

        def close(self) -> None:
            self.closed = True

    runtime = Runtime()

    class RuntimeAPI:
        @classmethod
        def from_pretrained(cls, *_args: Any, **_kwargs: Any) -> Runtime:
            return runtime

    monkeypatch.setattr(supertonicsynth, "SupertonicRuntime", RuntimeAPI)
    with pytest.raises(EngineBackendError) as error:
        SupertonicSynthEngineAdapter().open(_selection())
    assert error.value.code == "supertonic.target_changed"
    assert runtime.closed


def test_voice_style_and_request_errors_map_to_readio_errors() -> None:
    adapter = SupertonicSynthEngineAdapter()
    selection = _selection()
    with pytest.raises(InvalidEngineOptionError):
        adapter.open(replace(selection, options={"steps": 999}))


def test_registry_status_and_alias_are_available() -> None:
    from readio.engines.registry import EngineRegistry, normalize_engine_id

    assert normalize_engine_id("supertonicsynth") == "supertonic"
    registry = EngineRegistry()
    adapter = registry.get("supertonic")
    assert isinstance(adapter, SupertonicSynthEngineAdapter)
    status = registry.status()["supertonic"]
    assert status["package"] is True
    assert status["version"] == adapter.version()
    assert status["status"] == "ready"
