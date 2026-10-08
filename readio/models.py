"""Readio model projection and validation over generic synthesis targets."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .config import LanguageSettings, normalize_language_key
from .engines.registry import normalize_engine_id

logger = logging.getLogger(__name__)
_RUNTIME_SOURCES = {"github", "huggingface"}


class ModelDiscoveryError(ValueError):
    """A model registry could not be inspected or contained an invalid choice."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "readio.registry_unavailable",
        installed_version: str | None = None,
        distribution_version: str | None = None,
        module_version: str | None = None,
        module_path: str | None = None,
        missing_dependency: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.installed_version = installed_version
        self.distribution_version = distribution_version
        self.module_version = module_version
        self.module_path = module_path
        self.missing_dependency = missing_dependency


@dataclass(frozen=True, slots=True)
class VoiceMetadata:
    """Metadata describing one canonical PyKokoro voice."""

    id: str
    gender: str
    language: str
    locale: str
    language_label: str


@dataclass(frozen=True, slots=True)
class ModelInfo:
    id: str
    source: str
    languages: tuple[str, ...]
    voices: tuple[str, ...]
    default_voice: str
    qualities: tuple[str, ...]
    g2p_backend: str | None
    lexicons: tuple[str, ...] | None
    frontend: str
    status: str
    experimental: bool
    runtime_available: bool
    redistribution_allowed: bool
    distribution_id: str | None = None
    provider: str | None = None
    distribution_provider: str | None = None
    engine: str = "kokoro"
    sample_rate: int | None = None
    max_tokens: int | None = None
    voice_details: tuple[VoiceMetadata, ...] = ()

    @classmethod
    def from_target(cls, target: Any) -> ModelInfo:
        """Build the legacy model view from a neutral discovered target."""
        metadata = target.metadata
        voices = tuple(target.voices)
        details = tuple(
            VoiceMetadata(
                id=voice.id,
                gender=voice.gender,
                language=voice.language,
                locale=voice.locale,
                language_label=voice.language_label,
            )
            for voice in target.voice_details
        )
        lexicons = metadata.get("lexicons")
        return cls(
            id=target.id,
            source=str(metadata.get("source") or target.engine),
            languages=tuple(target.languages),
            voices=voices,
            default_voice=str(target.default_voice or (voices[0] if voices else "")),
            qualities=tuple(target.qualities),
            g2p_backend=metadata.get("g2p_backend"),
            lexicons=tuple(lexicons) if lexicons is not None else None,
            frontend=str(metadata.get("frontend") or ""),
            status=target.status,
            experimental=bool(metadata.get("experimental", target.status == "experimental")),
            runtime_available=target.runtime_available,
            redistribution_allowed=bool(metadata.get("redistribution_allowed", False)),
            distribution_id=metadata.get("distribution_id"),
            provider=metadata.get("provider"),
            distribution_provider=metadata.get("distribution_provider"),
            engine=target.engine,
            sample_rate=target.sample_rate or metadata.get("sample_rate"),
            max_tokens=metadata.get("max_tokens"),
            voice_details=details,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "languages": list(self.languages),
            "default_voice": self.default_voice,
            "voices": list(self.voices),
            "voice_details": [
                {
                    "id": detail.id,
                    "gender": detail.gender,
                    "language": detail.language,
                    "locale": detail.locale,
                    "language_label": detail.language_label,
                }
                for detail in self.voice_details
            ],
            "qualities": list(self.qualities),
            "g2p_backend": self.g2p_backend,
            "lexicons": list(self.lexicons) if self.lexicons is not None else None,
            "lexicons_known": self.lexicons is not None,
            "frontend": self.frontend,
            "experimental": self.experimental,
            "status": self.status,
            "runtime_available": self.runtime_available,
            "redistribution_allowed": self.redistribution_allowed,
            "distribution_id": self.distribution_id,
            "provider": self.provider,
            "distribution_provider": self.distribution_provider,
            "engine": self.engine,
            "sample_rate": self.sample_rate,
            "max_tokens": self.max_tokens,
        }


def language_matches(requested: str, declared: tuple[str, ...]) -> bool:
    requested = normalize_language_key(requested)
    requested_base = requested.partition("-")[0]
    return any(
        requested == normalize_language_key(item)
        or requested_base == normalize_language_key(item).partition("-")[0]
        for item in declared
    )


def discover_model_info(
    *,
    language: str | None = None,
    status: str | None = None,
    offline: bool = False,
    refresh: bool = False,
    preference: str = "auto",
    engine: str | None = None,
) -> tuple[tuple[ModelInfo, ...], Any]:
    """Discover engine targets through the unified synthesis registry."""
    from .engines.discovery import discover_targets

    selected_engine = normalize_engine_id(engine) if engine is not None else "kokoro"
    discovery = discover_targets(
        engine=selected_engine,
        language=language,
        offline=offline,
        refresh=refresh,
        preference=preference,
    )
    models = tuple(ModelInfo.from_target(target) for target in discovery.targets)
    if status is not None:
        models = tuple(item for item in models if item.status == status)
    return models, discovery


def get_model_info(
    model_id: str,
    *,
    offline: bool = False,
    refresh: bool = False,
    preference: str = "auto",
    engine: str | None = None,
) -> tuple[ModelInfo, Any]:
    logger.debug("model.resolve.start model=%s", model_id)
    models, result = discover_model_info(
        offline=offline, refresh=refresh, preference=preference, engine=engine
    )
    for model in models:
        if model.id == model_id:
            return model, result
    raise ModelDiscoveryError(
        f"Unknown model '{model_id}'. Run `readio models list` to inspect available models.",
        code="readio.model_not_found",
    )


def _validation_error_code(
    model: ModelInfo,
    generic: str,
    pykokoro: str,
) -> str:
    return pykokoro if model.engine == "kokoro" else generic


def validate_language_settings(
    language: str,
    settings: LanguageSettings,
    model: ModelInfo,
) -> LanguageSettings:
    """Validate a language profile against already-discovered model metadata."""
    normalized = normalize_language_key(language)
    if not model.runtime_available:
        raise ModelDiscoveryError(
            f"Model '{model.id}' is not available in the installed runtime.",
            code=_validation_error_code(
                model, "readio.model_unsupported", "pykokoro.model_unsupported"
            ),
        )
    if model.experimental and not settings.allow_experimental:
        raise ModelDiscoveryError(
            f"Model '{model.id}' uses an experimental PyKokoro frontend. "
            "Re-run with --allow-experimental or persist allow_experimental=true "
            "for this language profile.",
            code="pykokoro.experimental_required",
        )
    if model.status != "ready" and not (model.experimental and settings.allow_experimental):
        raise ModelDiscoveryError(
            f"Model '{model.id}' is not runnable: {model.status}",
            code=_validation_error_code(
                model, "readio.model_unsupported", "pykokoro.model_unsupported"
            ),
        )
    if not language_matches(normalized, model.languages):
        declared = ", ".join(model.languages) or "none"
        raise ModelDiscoveryError(
            f"Model '{model.id}' does not declare language '{normalized}'. Declared languages: {declared}",
            code=_validation_error_code(
                model, "readio.model_unsupported", "pykokoro.model_unsupported"
            ),
        )
    if (
        settings.source is not None
        and settings.source not in _RUNTIME_SOURCES
        and not (model.engine == "inflect" and settings.source == "inflectsynth")
    ):
        raise ModelDiscoveryError(
            f"Model source '{settings.source}' is not supported; use github or huggingface.",
            code=_validation_error_code(
                model, "readio.model_source_invalid", "pykokoro.model_source_invalid"
            ),
        )
    if settings.source is not None and model.source != settings.source:
        raise ModelDiscoveryError(
            f"Model '{model.id}' resolved from {model.source!r}, not requested source "
            f"{settings.source!r}.",
            code=_validation_error_code(
                model, "readio.model_source_invalid", "pykokoro.model_source_invalid"
            ),
        )
    if settings.quality is not None and settings.quality not in model.qualities:
        available = ", ".join(model.qualities) or "none"
        raise ModelDiscoveryError(
            f"Quality '{settings.quality}' is not available for model '{model.id}'. Available qualities: {available}",
            code=_validation_error_code(
                model, "readio.quality_invalid", "pykokoro.quality_invalid"
            ),
        )
    if settings.voice is not None and settings.voice not in model.voices:
        available = ", ".join(model.voices) or "none"
        raise ModelDiscoveryError(
            f"Voice '{settings.voice}' is not available for model '{model.id}'. Available voices: {available}",
            code=_validation_error_code(model, "readio.voice_invalid", "pykokoro.voice_invalid"),
        )
    if settings.lexicons is not None and model.lexicons is not None:
        missing = tuple(item for item in settings.lexicons if item not in model.lexicons)
        if missing:
            available = ", ".join(model.lexicons) or "none"
            qualified = missing[0]
            named = qualified.rsplit(":", 1)[-1] if ":" in qualified else None
            if named and named in model.lexicons:
                message = (
                    f"Use the named lexicon {named!r}. {qualified!r} is the underlying "
                    "language-qualified Lexphon asset ID."
                )
            else:
                message = (
                    f"Lexicon '{qualified}' is not available for model '{model.id}' / "
                    f"language '{normalized}'. Available lexicons: {available}"
                )
            raise ModelDiscoveryError(
                message,
                code=_validation_error_code(
                    model, "readio.lexicon_invalid", "pykokoro.lexicon_invalid"
                ),
            )
    return settings


__all__ = [
    "ModelDiscoveryError",
    "ModelInfo",
    "VoiceMetadata",
    "discover_model_info",
    "get_model_info",
    "language_matches",
    "validate_language_settings",
]
