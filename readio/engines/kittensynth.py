"""Request-centric adapter for the public KittenSynth API."""

from __future__ import annotations

import importlib.metadata
import inspect
import math
from collections.abc import Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from onnxvoice import OnnxVoice, language_base, normalize_language_tag
from onnxvoice.errors import OnnxVoiceError
from onnxvoice.inventory import normalize_gender

from ..config import normalize_language_key
from ..errors import (
    EmptySpeechTextError,
    EngineBackendError,
    EngineSynthesisError,
    InvalidEngineModelError,
    InvalidEngineOptionError,
    InvalidEngineVoiceError,
)
from .base import EngineCapabilities, EngineSelection, RenderedSpeech, RequestMeasure, SpeechRequest
from .catalog import CatalogRequest, SynthesisTarget

DEFAULT_KITTEN_MODEL = "nano-0.8-int8"
DEFAULT_KITTEN_VOICE = "Jasper"
KITTEN_OPTION_NAMES = frozenset(
    {
        "speed",
        "quality",
        "cache_dir",
        "force_download",
        "providers",
        "provider_options",
        "session_options",
    }
)


class KittenSelectionError(ValueError):
    """A Kitten selection error with a stable plan diagnostic."""

    def __init__(self, code: str, message: str, field: str) -> None:
        super().__init__(message)
        self.diagnostic_code = code
        self.diagnostic_field = field


def _is_english(language: str) -> bool:
    normalized = normalize_language_key(language)
    return normalized == "english" or language_base(normalized) == "en"


def _language_values(value: Any) -> tuple[str, str, str]:
    raw = str(value or "en")
    normalized = normalize_language_tag(raw)
    base = language_base(normalized) or normalize_language_key(raw)
    if base == "english":
        base = "en"
        normalized = "en"
    return base or "unknown", normalized or base or "unknown", raw


def _details_for_voices(records: tuple[Any, ...], target_id: str) -> list[dict[str, str]]:
    details: list[dict[str, str]] = []
    for record in records:
        catalog_item = getattr(record, "catalog_item", None)
        if getattr(catalog_item, "id", None) != target_id:
            continue
        metadata = getattr(record, "metadata", None)
        metadata = metadata if isinstance(metadata, Mapping) else {}
        voice_id = getattr(record, "voice_id", None)
        if not isinstance(voice_id, str) or not voice_id:
            continue
        languages = tuple(getattr(record, "languages", ()) or ())
        base, locale, raw = _language_values(languages[0] if languages else "en")
        details.append(
            {
                "id": voice_id,
                "gender": normalize_gender(metadata.get("gender")),
                "language": base,
                "locale": locale,
                "language_label": str(metadata.get("language_label") or raw),
            }
        )
    return details


def _target_from_item(item: Any, voice_records: tuple[Any, ...] = ()) -> SynthesisTarget:
    metadata = dict(item.metadata) if isinstance(item.metadata, Mapping) else {}
    language, locale, raw_language = _language_values(metadata.get("language"))
    quality = metadata.get("quality")
    voices = tuple(str(voice) for voice in getattr(item, "voices", ()) if isinstance(voice, str))
    sample_rate = getattr(item, "sample_rate", None)
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate <= 0:
        sample_rate = None
    display_name = str(metadata.get("name") or item.id)
    model_version = metadata.get("version")
    if model_version:
        display_name = f"{display_name} {model_version}"
    normalized_metadata = {
        **metadata,
        "language": language,
        "locale": locale,
        "language_label": raw_language if raw_language.casefold() != "en" else "English",
        "voice_details": _details_for_voices(voice_records, str(item.id)),
        "default_voice": DEFAULT_KITTEN_VOICE if DEFAULT_KITTEN_VOICE in voices else None,
        "quality": quality,
        "source": "onnxvoice",
    }
    return SynthesisTarget(
        engine="kitten",
        id=str(item.id),
        display_name=display_name,
        languages=(locale,) if locale and locale != "unknown" else (),
        status="ready",
        runtime_available=True,
        sample_rate=sample_rate,
        voices=voices,
        qualities=(str(quality),) if isinstance(quality, str) and quality else (),
        aliases=tuple(str(alias) for alias in getattr(item, "aliases", ()) or ()),
        capabilities=frozenset({"named_voices"}),
        metadata=normalized_metadata,
    )


def _catalog_targets(request: CatalogRequest) -> tuple[SynthesisTarget, ...]:
    manager = OnnxVoice(offline=request.offline)
    items = manager.list(
        "kitten",
        language=request.language,
        refresh=request.refresh,
    )
    # This remains on the public OnnxVoice API; it supplies descriptive metadata
    # without inferring demographics from public voice names.
    records = tuple(
        manager.list_voices(
            "kitten",
            language=request.language,
            refresh=False,
        )
    )
    return tuple(_target_from_item(item, records) for item in items if item.system == "kitten")


def _kitten_version() -> str | None:
    try:
        return importlib.metadata.version("kittensynth")
    except importlib.metadata.PackageNotFoundError:
        return None


def _dependency_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _translate_kitten_error(
    error: Exception,
    selection: EngineSelection,
    request: SpeechRequest | None = None,
    *,
    opening: bool = False,
) -> EngineSynthesisError:
    try:
        import kittensynth
    except ImportError:
        kittensynth = None  # type: ignore[assignment]

    error_map: tuple[tuple[str, type[EngineSynthesisError], str | None], ...] = (
        ("EmptyTextError", EmptySpeechTextError, None),
        ("InvalidSpeedError", InvalidEngineOptionError, "kitten.invalid_speed"),
        ("InvalidVoiceError", InvalidEngineVoiceError, "kitten.voice_unavailable"),
        ("UnsupportedModelError", InvalidEngineModelError, "kitten.model_unsupported"),
        ("OnnxVoiceContractError", EngineBackendError, "kitten.runtime_contract_error"),
    )
    error_type: type[EngineSynthesisError] = EngineBackendError
    code: str | None = "kitten.engine_error"
    if kittensynth is not None:
        for native_name, mapped_type, mapped_code in error_map:
            native_type = getattr(kittensynth, native_name, None)
            if isinstance(native_type, type) and isinstance(error, native_type):
                error_type = mapped_type
                code = mapped_code
                break
        else:
            if opening and isinstance(error, (TypeError, ValueError)):
                error_type = InvalidEngineOptionError
                code = "kitten.invalid_options"
    context: dict[str, Any] = {
        "engine": "kitten",
        "engine_version": _kitten_version(),
        "target_id": selection.target_id,
        "language": request.language if request is not None else selection.language,
        "voice": request.voice if request is not None else selection.voice,
        "request_id": request.id if request is not None else None,
        "native_error_type": type(error).__name__,
    }
    return error_type(str(error), **context, code=code)


class KittenSynthEngineSession:
    """Adapt prepared Readio requests to one open ``KittenVoice``."""

    def __init__(self, voice: Any, selection: EngineSelection) -> None:
        self._voice = voice
        self._selection = selection

    def measure(self, request: SpeechRequest) -> RequestMeasure:
        return RequestMeasure(
            fits=None,
            amount=None,
            maximum=None,
            unit="unknown",
            source="kittensynth.no_capacity_measurement",
        )

    def synthesize(self, request: SpeechRequest) -> RenderedSpeech:
        speed = float(self._selection.options.get("speed", 1.0))
        try:
            result = self._voice.synthesize_prepared(
                request.text,
                voice=request.voice or self._selection.voice or DEFAULT_KITTEN_VOICE,
                speed=speed,
            )
        except EngineSynthesisError:
            raise
        except Exception as exc:
            raise _translate_kitten_error(exc, self._selection, request) from exc
        metadata = dict(result.metadata or {})
        metadata.update(
            {
                "voice": result.voice,
                "model_ref": result.model_ref,
                "effective_speed": result.speed,
            }
        )
        return RenderedSpeech(
            id=request.id,
            audio=result.audio,
            sample_rate=result.sample_rate,
            warnings=(),
            word_timings=(),
            metadata=metadata,
        )


class KittenSynthEngineAdapter:
    """Kitten model discovery, selection, runtime and request adaptation."""

    id = "kitten"
    package_name = "kittensynth"

    def version(self) -> str | None:
        return _kitten_version()

    def compatible_api(self) -> bool:
        try:
            import kittensynth

            required = ("KittenVoice", "SynthesisConfig", "SynthesisResult")
            if not all(hasattr(kittensynth, name) for name in required):
                return False
            voice_type = kittensynth.KittenVoice
            synth_parameters = inspect.signature(voice_type.synthesize_prepared).parameters
            pretrained_parameters = inspect.signature(voice_type.from_pretrained).parameters
            required_keywords = ("voice", "speed", "config")
            if not all(
                name in synth_parameters
                and synth_parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
                for name in required_keywords
            ):
                return False
            if not all(
                name in pretrained_parameters
                for name in (
                    "quality",
                    "cache_dir",
                    "offline",
                    "refresh_catalog",
                    "force_download",
                    "providers",
                    "provider_options",
                    "session_options",
                )
            ):
                return False
            return callable(getattr(OnnxVoice, "list", None)) and callable(
                getattr(OnnxVoice, "list_voices", None)
            )
        except (ImportError, AttributeError, TypeError, ValueError, OSError, RuntimeError):
            return False

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            id=self.id,
            voice_binding_namespace="kitten",
            voice_binding_scope="request",
            option_names=KITTEN_OPTION_NAMES,
            supports_named_voices=True,
            supports_reference_voice=False,
            supports_speakers=False,
            supports_pronunciation_overrides=False,
            supports_linguistic_tokens=False,
            supports_whole_request_phonemes=False,
            supports_lexicons=False,
            supports_model_sources=False,
            supports_qualities=True,
            supports_live=True,
            supports_timestamps=False,
            supports_voice_level_calibration=False,
            supports_request_measurement=False,
        )

    def discover(self, request: CatalogRequest) -> tuple[SynthesisTarget, ...]:
        try:
            return _catalog_targets(request)
        except ImportError:
            return ()

    def resolve(self, request: Any) -> tuple[EngineSelection, tuple[Any, ...]]:
        language = normalize_language_key(getattr(request, "language", None) or "en-us")
        if not _is_english(language):
            raise KittenSelectionError(
                "kitten.language_incompatible",
                f"KittenSynth supports English only, not {language!r}.",
                "synthesis.language",
            )
        options = dict(getattr(request, "options", {}) or {})
        options.update(dict(getattr(request, "engine_options", {}) or {}))
        speed = options.get("speed", 1.0)
        try:
            speed = float(speed)
        except (TypeError, ValueError) as exc:
            raise KittenSelectionError(
                "kitten.invalid_speed",
                "Kitten speed must be finite and greater than zero.",
                "synthesis.speed",
            ) from exc
        if not math.isfinite(speed) or speed <= 0:
            raise KittenSelectionError(
                "kitten.invalid_speed",
                "Kitten speed must be finite and greater than zero.",
                "synthesis.speed",
            )
        options["speed"] = speed
        target_id = (
            getattr(request, "target_id", None)
            or options.pop("model", None)
            or DEFAULT_KITTEN_MODEL
        )
        voice = (
            getattr(request, "voice", None) or options.pop("voice", None) or DEFAULT_KITTEN_VOICE
        )
        return (
            EngineSelection(
                engine=self.id,
                target_id=str(target_id),
                language=language,
                voice=str(voice),
                speaker=getattr(request, "speaker", None),
                options={
                    key: value for key, value in options.items() if key in KITTEN_OPTION_NAMES
                },
                offline=bool(getattr(request, "offline", False)),
                refresh=bool(getattr(request, "refresh", False)),
            ),
            (),
        )

    def validate_selection(self, selection: EngineSelection) -> tuple[Any, ...]:
        from ..plan import PlanDiagnostic

        if not _is_english(selection.language):
            return (
                PlanDiagnostic(
                    code="kitten.language_incompatible",
                    severity="error",
                    message=f"KittenSynth supports English only, not {selection.language!r}.",
                    field="synthesis.language",
                ),
            )
        try:
            targets = self.discover(
                CatalogRequest(
                    engine=self.id,
                    language=selection.language,
                    offline=selection.offline,
                    refresh=selection.refresh,
                )
            )
        except (ImportError, OnnxVoiceError, OSError, RuntimeError, TypeError, ValueError) as exc:
            return (
                PlanDiagnostic(
                    code="kitten.engine_unavailable",
                    severity="error",
                    message=f"Kitten model catalog is unavailable: {exc}",
                    field="synthesis.engine",
                ),
            )
        target = next(
            (
                item
                for item in targets
                if item.id == selection.target_id or selection.target_id in item.aliases
            ),
            None,
        )
        if target is None:
            return (
                PlanDiagnostic(
                    code="kitten.model_not_found",
                    severity="error",
                    message=f"Kitten model {selection.target_id!r} was not found in the OnnxVoice catalog.",
                    field="render.target.id",
                ),
            )
        if target.engine != "kitten":
            return (
                PlanDiagnostic(
                    code="kitten.model_not_found",
                    severity="error",
                    message=f"Catalog target {target.id!r} does not belong to the Kitten system.",
                    field="render.target.id",
                ),
            )
        if not target.sample_rate or target.sample_rate <= 0:
            return (
                PlanDiagnostic(
                    code="kitten.model_not_found",
                    severity="error",
                    message=f"Kitten model {target.id!r} has no valid sample rate.",
                    field="render.target.id",
                ),
            )
        if selection.voice not in target.voices:
            return (
                PlanDiagnostic(
                    code="kitten.voice_unavailable",
                    severity="error",
                    message=f"Voice {selection.voice!r} is not available for Kitten model {target.id!r}.",
                    field="render.target.voice",
                ),
            )
        quality = selection.options.get("quality")
        if quality is not None and quality not in target.qualities:
            return (
                PlanDiagnostic(
                    code="kitten.quality_unavailable",
                    severity="error",
                    message=f"Quality {quality!r} is not available for Kitten model {target.id!r}.",
                    field="render.options.quality",
                ),
            )
        return ()

    def target_metadata(self, selection: EngineSelection) -> Mapping[str, Any]:
        targets = self.discover(
            CatalogRequest(
                engine=self.id,
                language=selection.language,
                offline=selection.offline,
                refresh=selection.refresh,
            )
        )
        target = next(
            (
                item
                for item in targets
                if item.id == selection.target_id or selection.target_id in item.aliases
            ),
            None,
        )
        if target is None:
            return {}
        return {
            **dict(target.metadata),
            "sample_rate": target.sample_rate,
            "voices": target.voices,
            "qualities": target.qualities,
            "source_revision": target.metadata.get("source_revision"),
        }

    def canonical_synthesis_identity(self, selection: EngineSelection) -> Mapping[str, Any]:
        pcm_options = {
            key: selection.options[key]
            for key in ("quality", "speed", "providers", "provider_options", "session_options")
            if key in selection.options
        }
        return {
            "engine": self.id,
            "engine_version": self.version(),
            "onnxvoice_version": _dependency_version("onnxvoice"),
            "g2p_version": _dependency_version("kitteng2p"),
            "target_id": selection.target_id,
            "voice": selection.voice,
            "language": selection.language,
            "options": pcm_options,
            "model_revision": selection.metadata.get("source_revision"),
        }

    def open(self, selection: EngineSelection) -> AbstractContextManager[KittenSynthEngineSession]:
        import kittensynth

        options = dict(selection.options)
        try:
            voice = kittensynth.KittenVoice.from_pretrained(
                selection.target_id,
                quality=options.get("quality"),
                cache_dir=options.get("cache_dir"),
                offline=selection.offline,
                refresh_catalog=selection.refresh,
                force_download=bool(options.get("force_download", False)),
                providers=options.get("providers"),
                provider_options=options.get("provider_options"),
                session_options=options.get("session_options"),
            )
        except Exception as exc:
            raise _translate_kitten_error(exc, selection, opening=True) from exc

        @contextmanager
        def session() -> Any:
            try:
                yield KittenSynthEngineSession(voice, selection)
            finally:
                voice.close()

        return session()


__all__ = [
    "DEFAULT_KITTEN_MODEL",
    "DEFAULT_KITTEN_VOICE",
    "KittenSynthEngineAdapter",
    "KittenSynthEngineSession",
]
