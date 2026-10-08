from __future__ import annotations

import sys
from dataclasses import dataclass
from types import ModuleType, SimpleNamespace
from typing import Any, ClassVar

import numpy as np
import pytest

from readio.engines.base import EngineSelection, RequestMeasure, SpeechRequest, SpeechToken
from readio.engines.catalog import CatalogRequest
from readio.engines.inflectsynth import (
    INFLECT_OPTION_NAMES,
    InflectSelectionError,
    InflectSynthEngineAdapter,
    InflectSynthEngineSession,
)
from readio.engines.selection import EngineRequest
from readio.errors import (
    EmptySpeechTextError,
    EngineBackendError,
    InvalidEngineLanguageError,
    InvalidEngineModelError,
    InvalidEngineOptionError,
    InvalidEngineVoiceError,
    InvalidSpeechRequestError,
    SpeechRequestTooLongError,
    UnsupportedSynthesisFeatureError,
)


class NativeError(Exception):
    pass


class NativeCapacityError(NativeError):
    def __init__(self) -> None:
        super().__init__("native request is too long")
        self.token_count = 30
        self.max_tokens = 20
        self.text_length = 81
        self.model_id = "nano-v2"


class NativeVoiceError(NativeError):
    pass


class NativeInferenceError(NativeError):
    pass


@dataclass(frozen=True)
class NativeVoiceLevelConfig:
    mode: str = "off"


@dataclass(frozen=True)
class NativeSynthesisConfig:
    speed: float = 1.0
    variation: float = 0.667
    seed: int = 0
    voice_level: NativeVoiceLevelConfig = NativeVoiceLevelConfig()

    def validated(self) -> NativeSynthesisConfig:
        if not 0.5 <= self.speed <= 2.0:
            raise ValueError("speed must be between 0.5 and 2.0")
        if not 0 <= self.variation <= 1:
            raise ValueError("variation must be between 0 and 1")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool) or self.seed < 0:
            raise ValueError("seed must be non-negative")
        if self.voice_level.mode not in {"off", "calibrated"}:
            raise ValueError("invalid voice_level")
        return self


class NativeVoice:
    opened = 0
    closed = 0
    open_kwargs: ClassVar[dict[str, Any]] = {}
    instance: ClassVar[NativeVoice | None] = None

    @classmethod
    def from_pretrained(cls, model: str, **kwargs: Any) -> NativeVoice:
        cls.opened += 1
        cls.open_kwargs = {"model": model, **kwargs}
        cls.instance = cls()
        return cls.instance

    @classmethod
    def from_local(cls, **_kwargs: Any) -> NativeVoice:
        return cls()

    def __init__(self) -> None:
        self.model_id = "nano-v2"
        self.metadata: dict[str, Any] = {"source_revision": "revision-1"}
        self.close_count = 0
        self.synthesis_calls: list[tuple[str, str, Any]] = []
        self.measure_calls: list[str] = []
        self.result: Any = None
        self.synthesis_error: Exception | None = None
        self.measure_result: Any = None
        self.measure_error: Exception | None = None

    def synthesize_prepared(self, text: str, *, voice: str, config: Any) -> Any:
        self.synthesis_calls.append((text, voice, config))
        if self.synthesis_error is not None:
            raise self.synthesis_error
        if self.result is not None:
            return self.result
        return SimpleNamespace(
            audio=np.asarray([0.25, -0.25], dtype=np.float32),
            sample_rate=24_000,
            model_id=self.model_id,
            voice=voice,
            speed=config.speed,
            variation=config.variation,
            seed=config.seed,
            model_ref="catalog:nano-v2",
            metadata={
                "normalized_text": text.lower(),
                "phoneme_text": "həˈloʊ",
                "token_count": 2,
                "revision": "revision-1",
                "voice_level": {
                    "mode": config.voice_level.mode,
                    "applied": config.voice_level.mode == "calibrated",
                    "gain_db": 1.5,
                    "calibration_key": "inflect:nano-v2:default",
                    "catalog_revision": "calibration-1",
                    "reason": "test",
                },
            },
        )

    def measure_prepared(self, text: str) -> Any:
        self.measure_calls.append(text)
        if self.measure_error is not None:
            raise self.measure_error
        return self.measure_result or SimpleNamespace(
            fits=True, amount=10, maximum=20, model_id=self.model_id
        )

    def close(self) -> None:
        self.close_count += 1


@dataclass(frozen=True)
class DescribedVoice:
    id: str = "default"
    gender: str = "unknown"
    language: str = "en-US"
    locale: str = "en-US"
    language_label: str = "English"
    languages: tuple[str, ...] = ("en-US",)


@dataclass(frozen=True)
class DiscoveredModel:
    id: str
    display_name: str
    version: str
    language: str
    sample_rate: int
    aliases: tuple[str, ...]
    voices: tuple[DescribedVoice, ...]
    default_voice: str
    source_revision: str
    metadata: dict[str, Any]
    runtime_available: bool = True


def _models() -> tuple[DiscoveredModel, ...]:
    return (
        DiscoveredModel(
            id="nano-v2",
            display_name="Inflect Nano v2",
            version="2",
            language="en-US",
            sample_rate=24_000,
            aliases=("nano", "Inflect-Nano-v2-ONNX"),
            voices=(DescribedVoice(),),
            default_voice="default",
            source_revision="revision-1",
            metadata={"max_input_tokens": 256, "catalog_field": "preserved"},
        ),
        DiscoveredModel(
            id="micro-v2",
            display_name="Inflect Micro v2",
            version="2",
            language="en-US",
            sample_rate=24_000,
            aliases=("micro", "Inflect-Micro-v2-ONNX"),
            voices=(DescribedVoice(),),
            default_voice="default",
            source_revision="revision-2",
            metadata={},
        ),
    )


def _fake_package(*, known_capacity: bool = False) -> ModuleType:
    module = ModuleType("inflectsynth")
    module.__version__ = "0.1.1"
    module.DEFAULT_MODEL = "nano-v2"
    module.DEFAULT_VOICE = "default"
    module.REQUEST_API_VERSION = 1
    module.CAPACITY_API_VERSION = 1
    module.InflectSynthError = NativeError
    module.SynthesisInputTooLongError = NativeCapacityError
    module.EmptyTextError = type("EmptyTextError", (NativeError,), {})
    module.TextPreparationError = type("TextPreparationError", (NativeError,), {})
    module.InvalidSpeedError = type("InvalidSpeedError", (NativeError,), {})
    module.InvalidVariationError = type("InvalidVariationError", (NativeError,), {})
    module.InvalidSeedError = type("InvalidSeedError", (NativeError,), {})
    module.InvalidSynthesisConfigError = type("InvalidSynthesisConfigError", (NativeError,), {})
    module.InvalidVoiceError = NativeVoiceError
    module.UnsupportedModelError = type("UnsupportedModelError", (NativeError,), {})
    module.CatalogDiscoveryError = type("CatalogDiscoveryError", (NativeError,), {})
    module.CatalogUnavailableError = type(
        "CatalogUnavailableError", (module.CatalogDiscoveryError,), {}
    )
    module.OnnxVoiceContractError = type("OnnxVoiceContractError", (NativeError,), {})
    module.ModelInferenceError = NativeInferenceError
    module.InflectVoice = NativeVoice
    module.SynthesisConfig = NativeSynthesisConfig
    module.SynthesisResult = type("SynthesisResult", (), {})
    module.VoiceLevelConfig = NativeVoiceLevelConfig
    module.RequestMeasure = type("RequestMeasure", (), {})
    module.DiscoveredModel = DiscoveredModel
    module.DescribedVoice = DescribedVoice
    module.discovered_calls = []
    module.discover_models = lambda **kwargs: module.discovered_calls.append(kwargs) or _models()
    module.runtime_identity = lambda: {
        "engine": "inflect",
        "request_api_version": "1",
        "runtime_revision": "fake-runtime",
    }
    module.request_api_contract = lambda: {"entrypoint": "InflectVoice.synthesize_prepared"}
    module.capacity_api_contract = lambda: {
        "entrypoint": "InflectVoice.measure_prepared",
        "unit": "model_tokens",
        "supports_known_maximum": known_capacity,
        "policy_revision": "capacity-1",
    }
    module.default_voice_calibration = lambda: SimpleNamespace(revision="calibration-1")
    return module


@pytest.fixture
def fake_inflect(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    NativeVoice.opened = 0
    NativeVoice.closed = 0
    NativeVoice.open_kwargs = {}
    NativeVoice.instance = None
    module = _fake_package()
    monkeypatch.setitem(sys.modules, "inflectsynth", module)
    return module


def _selection(**changes: Any) -> EngineSelection:
    values: dict[str, Any] = {
        "engine": "inflect",
        "target_id": "nano-v2",
        "language": "en-us",
        "voice": "default",
        "options": {
            "speed": 1.0,
            "variation": 0.667,
            "seed": 0,
            "voice_level": "off",
        },
        "metadata": {"source_revision": "revision-1"},
    }
    values.update(changes)
    return EngineSelection(**values)


def test_public_api_probe_is_metadata_only_and_detects_capacity_contract(fake_inflect) -> None:
    adapter = InflectSynthEngineAdapter()
    probe = adapter.probe_api()
    assert probe.compatible
    assert probe.api_version == 1
    assert probe.details["capacity_api_version"] == 1
    assert probe.details["capacity_contract"]["supports_known_maximum"] is False
    assert NativeVoice.opened == 0
    assert fake_inflect.discovered_calls == []


def test_capabilities_match_request_contract_and_unknown_capacity(fake_inflect) -> None:
    capabilities = InflectSynthEngineAdapter().capabilities()
    assert capabilities.id == "inflect"
    assert capabilities.voice_binding_namespace == "inflect"
    assert capabilities.voice_binding_scope == "request"
    assert capabilities.option_names == INFLECT_OPTION_NAMES
    assert capabilities.supports_named_voices
    assert capabilities.supports_live
    assert capabilities.supports_voice_level_calibration
    assert not capabilities.supports_request_measurement
    assert not capabilities.supports_reference_voice
    assert not capabilities.supports_speakers
    assert not capabilities.supports_pronunciation_overrides
    assert not capabilities.supports_linguistic_tokens
    assert not capabilities.supports_whole_request_phonemes
    assert not capabilities.supports_lexicons
    assert not capabilities.supports_model_sources
    assert not capabilities.supports_qualities
    assert not capabilities.supports_timestamps
    fake_inflect.CAPACITY_API_VERSION = 1
    fake_inflect.capacity_api_contract = lambda: {"supports_known_maximum": True}
    assert InflectSynthEngineAdapter().capabilities().supports_request_measurement


def test_discovery_maps_targets_aliases_voice_metadata_and_language_filter(fake_inflect) -> None:
    targets = InflectSynthEngineAdapter().discover(
        CatalogRequest(engine="inflect", language="en-us", offline=True, refresh=True)
    )
    assert [target.id for target in targets] == ["nano-v2", "micro-v2"]
    assert targets[0].engine == "inflect"
    assert targets[0].aliases == ("nano", "Inflect-Nano-v2-ONNX")
    assert targets[0].languages == ("en",)
    assert targets[0].sample_rate == 24_000
    assert targets[0].voices == ("default",)
    assert targets[0].default_voice == "default"
    assert targets[0].voice_details[0].gender == "unknown"
    assert targets[0].voice_details[0].locale == "en-us"
    assert targets[0].metadata["source_revision"] == "revision-1"
    assert targets[0].metadata["max_input_tokens"] == 256
    assert targets[0].metadata["catalog_field"] == "preserved"
    assert fake_inflect.discovered_calls == [
        {
            "language": "en-us",
            "offline": True,
            "refresh": True,
            "cache_dir": None,
            "catalog_url": None,
        }
    ]
    assert NativeVoice.opened == 0


def test_resolve_defaults_canonicalizes_aliases_and_validates_controls(fake_inflect) -> None:
    adapter = InflectSynthEngineAdapter()
    default, _ = adapter.resolve(EngineRequest(engine="inflect"))
    assert default.target_id == "nano-v2"
    assert default.voice == "default"
    assert default.language == "en-us"

    for alias, canonical in (
        ("nano", "nano-v2"),
        ("micro", "micro-v2"),
        ("Inflect-Nano-v2-ONNX", "nano-v2"),
    ):
        selection, _ = adapter.resolve(EngineRequest(engine="inflect", target_id=alias))
        assert selection.target_id == canonical

    selection, _ = adapter.resolve(
        EngineRequest(
            engine="inflect",
            target_id="micro-v2",
            language="en_US",
            options={"speed": 1.2, "voice_level": "calibrated"},
            engine_options={"variation": 0.4, "seed": 23},
        )
    )
    assert selection.target_id == "micro-v2"
    assert selection.language == "en-us"
    assert selection.options["speed"] == 1.2
    assert selection.options["variation"] == 0.4
    assert selection.options["seed"] == 23
    assert selection.options["voice_level"] == "calibrated"


def test_resolve_rejects_non_english_and_invalid_or_unknown_options(fake_inflect) -> None:
    adapter = InflectSynthEngineAdapter()
    for language in ("de", "fr"):
        with pytest.raises(InflectSelectionError) as error:
            adapter.resolve(EngineRequest(engine="inflect", language=language))
        assert error.value.diagnostic_code == "inflect.language_unsupported"
    with pytest.raises(InflectSelectionError) as error:
        adapter.resolve(EngineRequest(engine="inflect", engine_options={"g2p": object()}))
    assert error.value.diagnostic_code == "inflect.invalid_option"
    with pytest.raises(InflectSelectionError, match="speed must be between") as error:
        adapter.resolve(EngineRequest(engine="inflect", engine_options={"speed": 9}))
    assert error.value.diagnostic_code == "inflect.invalid_option"
    with pytest.raises(InflectSelectionError, match="variation must be between"):
        adapter.resolve(EngineRequest(engine="inflect", engine_options={"variation": 2}))
    with pytest.raises(InflectSelectionError, match="seed must be non-negative"):
        adapter.resolve(EngineRequest(engine="inflect", engine_options={"seed": -1}))


def test_selection_validation_checks_model_runtime_voice_and_capacity(fake_inflect) -> None:
    adapter = InflectSynthEngineAdapter()
    selection = _selection(target_id="not-a-model", voice="missing")
    assert [item.code for item in adapter.validate_selection(selection)] == [
        "inflect.model_not_found"
    ]

    unavailable = _models()[0]
    object.__setattr__(unavailable, "runtime_available", False)
    fake_inflect.discover_models = lambda **_kwargs: (unavailable,)
    selection = _selection(voice="other")
    assert [item.code for item in adapter.validate_selection(selection)] == [
        "inflect.runtime_unavailable",
        "inflect.voice_unavailable",
    ]


def test_known_capacity_maps_public_measure_and_typed_oversize(fake_inflect) -> None:
    fake_inflect.capacity_api_contract = lambda: {"supports_known_maximum": True}
    voice = NativeVoice()
    session = InflectSynthEngineSession(voice, _selection(), NativeSynthesisConfig())
    measured = session.measure(SpeechRequest("parent", "An exact request.", "en-us"))
    assert voice.measure_calls == ["An exact request."]
    assert measured == RequestMeasure(
        fits=True,
        amount=10,
        maximum=20,
        unit="model_tokens",
        source="inflectsynth.measure_prepared",
        details={"model_id": "nano-v2"},
    )

    voice.synthesis_error = NativeCapacityError()
    with pytest.raises(SpeechRequestTooLongError) as error:
        session.synthesize(SpeechRequest("child", "oversize", "en-us"))
    assert error.value.amount == 30
    assert error.value.maximum == 20
    assert error.value.unit == "model_tokens"
    assert error.value.text_length == 81


def test_unknown_capacity_stays_fully_unknown_and_does_not_measure(fake_inflect) -> None:
    voice = NativeVoice()
    voice.measure_result = SimpleNamespace(fits=None, amount=12, maximum=None, model_id="nano-v2")
    session = InflectSynthEngineSession(voice, _selection(), NativeSynthesisConfig())
    measured = session.measure(SpeechRequest("parent", "Some text", "en"))
    assert measured.fits is None
    assert measured.amount is None
    assert measured.maximum is None
    assert measured.unit == "unknown"
    assert measured.source == "inflectsynth.no_capacity_measurement"
    assert voice.measure_calls == ["Some text"]


def test_synthesis_forwards_one_exact_request_and_preserves_metadata(fake_inflect) -> None:
    voice = NativeVoice()
    config = NativeSynthesisConfig(1.1, 0.25, 17, NativeVoiceLevelConfig("calibrated"))
    session = InflectSynthEngineSession(voice, _selection(voice="default"), config)
    text = "  Preserve this exact request: punctuation, case, and spaces!  "
    rendered = session.synthesize(SpeechRequest("child-1", text, "en_US"))
    assert len(voice.synthesis_calls) == 1
    exact_text, selected_voice, passed_config = voice.synthesis_calls[0]
    assert exact_text == text
    assert selected_voice == "default"
    assert passed_config is config
    np.testing.assert_array_equal(rendered.audio, [0.25, -0.25])
    assert rendered.id == "child-1"
    assert rendered.sample_rate == 24_000
    assert rendered.word_timings == ()
    assert rendered.warnings == ()
    assert rendered.metadata["normalized_text"] == text.lower()
    assert rendered.metadata["phoneme_text"] == "həˈloʊ"
    assert rendered.metadata["token_count"] == 2
    assert rendered.metadata["revision"] == "revision-1"
    assert rendered.metadata["model_ref"] == "catalog:nano-v2"
    assert rendered.metadata["source_revision"] == "revision-1"
    assert rendered.metadata["effective_speed"] == 1.1
    assert rendered.metadata["variation"] == 0.25
    assert rendered.metadata["seed"] == 17
    assert rendered.metadata["voice_level"]["mode"] == "calibrated"
    assert rendered.metadata["voice_level"]["applied"]
    assert rendered.metadata["voice_level"]["calibration_identity"] == "inflect:nano-v2:default"
    assert rendered.metadata["voice_level"]["calibration_revision"] == "calibration-1"


@pytest.mark.parametrize(
    "speech_request",
    [
        SpeechRequest("x", "text", "en", tokens=(SpeechToken(0, 4, "text"),)),
        SpeechRequest("x", "text", "en", pronunciation_overrides=(object(),)),
        SpeechRequest("x", "text", "en", whole_request_phonemes="t ɛ s t"),
        SpeechRequest("x", "text", "en", speaker="speaker-1"),
        SpeechRequest("x", "text", "de"),
    ],
)
def test_unsupported_semantics_never_call_native_synthesis(fake_inflect, speech_request) -> None:
    voice = NativeVoice()
    session = InflectSynthEngineSession(voice, _selection(), NativeSynthesisConfig())
    error_type = (
        InvalidEngineLanguageError
        if speech_request.language == "de"
        else UnsupportedSynthesisFeatureError
    )
    with pytest.raises(error_type):
        session.synthesize(speech_request)
    assert voice.synthesis_calls == []


def test_typed_errors_map_but_generic_inference_error_is_not_capacity(fake_inflect) -> None:
    voice = NativeVoice()
    session = InflectSynthEngineSession(voice, _selection(), NativeSynthesisConfig())
    voice.synthesis_error = NativeVoiceError("unknown voice")
    with pytest.raises(InvalidEngineVoiceError) as error:
        session.synthesize(SpeechRequest("x", "text", "en", voice="missing"))
    assert error.value.code == "inflect.voice_unavailable"

    voice.synthesis_error = NativeInferenceError("token length appears in this message")
    with pytest.raises(EngineBackendError) as error:
        session.synthesize(SpeechRequest("x", "text", "en"))
    assert error.value.code == "inflect.inference_failed"
    assert not isinstance(error.value, SpeechRequestTooLongError)

    voice.synthesis_error = ValueError("malformed request")
    with pytest.raises(InvalidSpeechRequestError) as error:
        session.synthesize(SpeechRequest("x", "text", "en"))
    assert error.value.code == "inflect.invalid_request"


def test_output_validation_rejects_invalid_pcm(fake_inflect) -> None:
    voice = NativeVoice()
    voice.result = SimpleNamespace(
        audio=np.asarray([float("nan")], dtype=np.float32),
        sample_rate=24_000,
        model_id="nano-v2",
        voice="default",
        speed=1.0,
        variation=0.667,
        seed=0,
        model_ref=None,
        metadata={},
    )
    session = InflectSynthEngineSession(voice, _selection(), NativeSynthesisConfig())
    with pytest.raises(EngineBackendError, match="finite"):
        session.synthesize(SpeechRequest("x", "text", "en"))


def test_open_forwards_managed_options_reuses_voice_and_closes_once(fake_inflect) -> None:
    selection = _selection(
        options={
            "speed": 1.1,
            "variation": 0.2,
            "seed": 5,
            "voice_level": "off",
            "cache_dir": "/tmp/cache",
            "force_download": True,
            "catalog_url": "https://example.invalid/catalog.json",
            "providers": "CPUExecutionProvider",
            "provider_options": {"CPUExecutionProvider": {}},
            "session_options": {"intra_op_num_threads": 1},
        },
        offline=True,
        refresh=True,
    )
    adapter = InflectSynthEngineAdapter()
    with adapter.open(selection) as session:
        assert isinstance(session, InflectSynthEngineSession)
        session.synthesize(SpeechRequest("one", "first", "en"))
        session.synthesize(SpeechRequest("two", "second", "en"))
    assert NativeVoice.opened == 1
    assert NativeVoice.closed == 0
    assert NativeVoice.instance is not None
    assert NativeVoice.instance.close_count == 1
    assert NativeVoice.open_kwargs == {
        "model": "nano-v2",
        "cache_dir": "/tmp/cache",
        "offline": True,
        "refresh_catalog": True,
        "force_download": True,
        "catalog_url": "https://example.invalid/catalog.json",
        "providers": "CPUExecutionProvider",
        "provider_options": {"CPUExecutionProvider": {}},
        "session_options": {"intra_op_num_threads": 1},
    }
    assert len(NativeVoice.instance.synthesis_calls) == 2


def test_open_closes_on_drift_and_reports_stable_code(fake_inflect) -> None:
    created: list[NativeVoice] = []

    class DriftedVoice(NativeVoice):
        @classmethod
        def from_pretrained(cls, _model: str, **_kwargs: Any) -> DriftedVoice:
            voice = cls()
            voice.model_id = "micro-v2"
            created.append(voice)
            return voice

    fake_inflect.InflectVoice = DriftedVoice
    with pytest.raises(EngineBackendError) as error:
        InflectSynthEngineAdapter().open(_selection())
    assert error.value.code == "inflect.target_changed"
    assert error.value.details["expected_source_revision"] == "revision-1"
    assert created[0].close_count == 1


def test_open_closes_on_revision_drift(fake_inflect) -> None:
    created: list[NativeVoice] = []

    class DriftedVoice(NativeVoice):
        @classmethod
        def from_pretrained(cls, _model: str, **_kwargs: Any) -> DriftedVoice:
            voice = cls()
            voice.metadata = {"source_revision": "revision-new"}
            created.append(voice)
            return voice

    fake_inflect.InflectVoice = DriftedVoice
    with pytest.raises(EngineBackendError) as error:
        InflectSynthEngineAdapter().open(_selection())
    assert error.value.code == "inflect.target_changed"
    assert created[0].close_count == 1


def test_canonical_identity_tracks_acoustic_settings_and_excludes_transport(fake_inflect) -> None:
    adapter = InflectSynthEngineAdapter()
    base = _selection()
    identity = adapter.canonical_synthesis_identity(base)
    assert identity["engine"] == "inflect"
    assert identity["engine_identity"]["runtime_revision"] == "fake-runtime"
    assert identity["voice_ref"] == "inflect:nano-v2/default"
    assert identity["source_revision"] == "revision-1"
    assert identity["capacity_policy_revision"] == "capacity-1"
    for updated in (
        _selection(target_id="micro-v2"),
        _selection(language="en-gb"),
        _selection(options={**base.options, "speed": 1.2}),
        _selection(options={**base.options, "variation": 0.3}),
        _selection(options={**base.options, "seed": 21}),
        _selection(options={**base.options, "voice_level": "calibrated"}),
        _selection(metadata={**base.metadata, "source_revision": "revision-2"}),
    ):
        assert adapter.canonical_synthesis_identity(updated) != identity

    calibrated = _selection(options={**base.options, "voice_level": "calibrated"})
    calibrated_identity = adapter.canonical_synthesis_identity(calibrated)
    assert calibrated_identity["voice_level"]["catalog_revision"] == "calibration-1"
    fake_inflect.default_voice_calibration = lambda: SimpleNamespace(revision="calibration-2")
    assert adapter.canonical_synthesis_identity(calibrated) != calibrated_identity
    transport_only = _selection(
        options={
            **base.options,
            "cache_dir": "/tmp/new-cache",
            "force_download": True,
            "catalog_url": "https://elsewhere.invalid",
        }
    )
    assert adapter.canonical_synthesis_identity(transport_only) == identity

    fake_inflect.capacity_api_contract = lambda: {
        "entrypoint": "InflectVoice.measure_prepared",
        "unit": "model_tokens",
        "supports_known_maximum": False,
        "policy_revision": "capacity-2",
    }
    assert adapter.canonical_synthesis_identity(base) != identity


def test_target_metadata_includes_only_publicly_known_positive_capacity(fake_inflect) -> None:
    adapter = InflectSynthEngineAdapter()
    selection = _selection()
    metadata = adapter.target_metadata(selection)
    assert metadata["max_input_tokens"] == 256
    assert metadata["source_revision"] == "revision-1"

    model = _models()[1]
    fake_inflect.discover_models = lambda **_kwargs: (model,)
    metadata = adapter.target_metadata(_selection(target_id="micro-v2"))
    assert "max_input_tokens" not in metadata


@pytest.mark.parametrize(
    ("native_name", "readio_error", "code"),
    [
        ("EmptyTextError", EmptySpeechTextError, "synthesis.empty_text"),
        ("TextPreparationError", InvalidSpeechRequestError, "inflect.text_preparation_failed"),
        ("InvalidSpeedError", InvalidEngineOptionError, "inflect.invalid_speed"),
        ("InvalidVariationError", InvalidEngineOptionError, "inflect.invalid_variation"),
        ("InvalidSeedError", InvalidEngineOptionError, "inflect.invalid_seed"),
        ("InvalidSynthesisConfigError", InvalidEngineOptionError, "inflect.invalid_options"),
        ("InvalidVoiceError", InvalidEngineVoiceError, "inflect.voice_unavailable"),
        ("UnsupportedModelError", InvalidEngineModelError, "inflect.model_unsupported"),
        ("CatalogUnavailableError", EngineBackendError, "inflect.catalog_unavailable"),
        ("CatalogDiscoveryError", EngineBackendError, "inflect.catalog_error"),
        ("OnnxVoiceContractError", EngineBackendError, "inflect.runtime_contract_error"),
        ("ModelInferenceError", EngineBackendError, "inflect.inference_failed"),
        ("InflectSynthError", EngineBackendError, "inflect.engine_error"),
    ],
)
def test_public_native_errors_have_stable_readio_mappings(
    fake_inflect, native_name: str, readio_error: type[Exception], code: str
) -> None:
    voice = NativeVoice()
    voice.synthesis_error = getattr(fake_inflect, native_name)("native failure")
    session = InflectSynthEngineSession(voice, _selection(), NativeSynthesisConfig())
    with pytest.raises(readio_error) as error:
        session.synthesize(SpeechRequest("x", "text", "en"))
    assert error.value.code == code


def test_inflect_capacity_drives_readios_legal_sentence_lowering(fake_inflect) -> None:
    from readio.rendering import CapacityContext, render_atomic_request

    fake_inflect.capacity_api_contract = lambda: {
        "supports_known_maximum": True,
        "unit": "model_tokens",
        "policy_revision": "capacity-1",
    }
    voice = NativeVoice()
    voice.measure_prepared = lambda text: SimpleNamespace(
        fits=len(text) <= 8,
        amount=len(text),
        maximum=8,
        model_id=voice.model_id,
    )
    session = InflectSynthEngineSession(voice, _selection(), NativeSynthesisConfig())
    request = SpeechRequest("parent", "First. Second.", "en-us")

    rendered = render_atomic_request(
        session,
        request,
        capacity=CapacityContext(planned_semantics=False),
    )

    children = [text for text, _voice, _config in voice.synthesis_calls]
    assert children == ["First. ", "Second."]
    assert "".join(children) == request.text
    assert len(rendered.requests) == 2
    assert all(item.measure.fits is True for item in rendered.requests)


def test_inflect_project_identity_reuses_and_invalidates_acoustic_controls(
    fake_inflect, monkeypatch, tmp_path
) -> None:
    from dataclasses import replace

    from readio.config import LanguageSettings, ReaderSettings, ReadioConfig
    from readio.engines.registry import _registry
    from readio.plan import InputRequest, OutputRequest, PlanRequest, SynthesisRequest
    from readio.project import init_project
    from readio.stages.planning import plan_project
    from readio.stages.synthesis import synthesize_project

    adapter = InflectSynthEngineAdapter()
    monkeypatch.setitem(_registry._adapters, "inflect", adapter)
    config = ReadioConfig(
        reader=ReaderSettings(engine="inflect", lang="en-us", voice="default", spacy="off"),
        languages={"en-us": LanguageSettings(engine="inflect", model="nano-v2", voice="default")},
        roles={},
    )
    source = tmp_path / "story.txt"
    source.write_text("A practical story. It has a second sentence.", encoding="utf-8")
    project = init_project(source, tmp_path / "story.readio")
    plan_project(project, config)

    request = PlanRequest(
        operation="render",
        input=InputRequest(project.document()),
        synthesis=SynthesisRequest(
            engine="inflect",
            model="nano-v2",
            language="en-us",
            voice="default",
            speed=1.0,
            engine_options={"variation": 0.3, "seed": 7},
        ),
        output=OutputRequest(mode="file", requested_format="wav", force=True),
    )
    first = synthesize_project(project, config, request=request)
    assert first["rendered"] >= 1
    route_identity = next(iter(first["profile"].payload["canonical"]["routes"].values()))
    assert route_identity["engine"] == "inflect"
    assert route_identity["voice_ref"] == "inflect:nano-v2/default"
    assert route_identity["source_revision"] == "revision-1"
    assert route_identity["engine_identity"]["runtime_revision"] == "fake-runtime"
    assert route_identity["capacity_policy_revision"] == "capacity-1"

    call_count = len(NativeVoice.instance.synthesis_calls)
    reused = synthesize_project(project, config, request=request)
    assert reused["rendered"] == 0
    assert reused["reused"] == first["rendered"]
    assert reused["profile"].profile_id == first["profile"].profile_id
    assert len(NativeVoice.instance.synthesis_calls) == call_count

    speed_request = replace(
        request,
        synthesis=replace(request.synthesis, speed=1.2),
    )
    speed_changed = synthesize_project(project, config, request=speed_request)
    assert speed_changed["rendered"] == first["rendered"]
    assert speed_changed["profile"].profile_id != first["profile"].profile_id

    variation_request = replace(
        speed_request,
        synthesis=replace(
            speed_request.synthesis,
            engine_options={"variation": 0.6, "seed": 7},
        ),
    )
    variation_changed = synthesize_project(project, config, request=variation_request)
    assert variation_changed["rendered"] == first["rendered"]
    assert variation_changed["profile"].profile_id != speed_changed["profile"].profile_id

    transport_request = replace(
        variation_request,
        synthesis=replace(
            variation_request.synthesis,
            engine_options={
                "variation": 0.6,
                "seed": 7,
                "cache_dir": "/tmp/other-cache",
                "force_download": True,
                "catalog_url": "https://example.invalid/catalog.json",
            },
        ),
    )
    transport_only = synthesize_project(project, config, request=transport_request)
    assert transport_only["rendered"] == 0
    assert transport_only["reused"] == variation_changed["rendered"]
    assert transport_only["profile"].profile_id == variation_changed["profile"].profile_id


def test_readio_live_incremental_requests_forward_inflect_controls(
    fake_inflect, monkeypatch
) -> None:
    from readio.api import Readio
    from readio.config import ReaderSettings, ReadioConfig
    from readio.engines.registry import _registry
    from readio.plan import SynthesisRequest

    class Sink:
        def __init__(self) -> None:
            self.writes = []

        def write(self, audio, sample_rate) -> None:
            self.writes.append((np.asarray(audio), sample_rate))

    monkeypatch.setitem(_registry._adapters, "inflect", InflectSynthEngineAdapter())
    app = Readio(
        ReadioConfig(reader=ReaderSettings(engine="inflect", lang="en-us", voice="default"))
    )
    sink = Sink()
    result = app.speech.render_live(
        ["First live paragraph.", "", "Second live paragraph."],
        sink,
        synthesis=SynthesisRequest(
            engine="inflect",
            model="nano-v2",
            language="en-us",
            voice="inflect:nano-v2/default",
            speed=1.2,
            engine_options={"variation": 0.4, "seed": 23},
        ),
    )

    assert result.summary.sample_count == 4
    assert [text for text, _voice, _config in NativeVoice.instance.synthesis_calls] == [
        "First live paragraph.",
        "Second live paragraph.",
    ]
    assert [call[2].speed for call in NativeVoice.instance.synthesis_calls] == [1.2, 1.2]
    assert [call[2].variation for call in NativeVoice.instance.synthesis_calls] == [0.4, 0.4]
    assert [call[2].seed for call in NativeVoice.instance.synthesis_calls] == [23, 23]
    assert len(sink.writes) == 2
    assert NativeVoice.opened == 1
    assert NativeVoice.instance.close_count == 1
