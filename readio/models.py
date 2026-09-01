"""Readio's adapter for the public, metadata-only PyKokoro discovery API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import LanguageSettings, normalize_language_key


class ModelDiscoveryError(ValueError):
    """A model registry could not be inspected or contained an invalid choice."""


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

    @classmethod
    def from_capabilities(cls, capabilities: Any) -> ModelInfo:
        return cls(
            id=capabilities.model_id,
            source=capabilities.source,
            languages=tuple(capabilities.languages),
            voices=tuple(capabilities.voices),
            default_voice=capabilities.default_voice,
            qualities=tuple(capabilities.qualities),
            g2p_backend=capabilities.g2p_backend,
            lexicons=None if capabilities.lexicons is None else tuple(capabilities.lexicons),
            frontend=capabilities.frontend,
            status=capabilities.status,
            experimental=capabilities.experimental,
            runtime_available=capabilities.runtime_available,
            redistribution_allowed=capabilities.redistribution_allowed,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "languages": list(self.languages),
            "default_voice": self.default_voice,
            "voices": list(self.voices),
            "qualities": list(self.qualities),
            "g2p_backend": self.g2p_backend,
            "lexicons": list(self.lexicons) if self.lexicons is not None else None,
            "lexicons_known": self.lexicons is not None,
            "frontend": self.frontend,
            "experimental": self.experimental,
            "status": self.status,
            "runtime_available": self.runtime_available,
            "redistribution_allowed": self.redistribution_allowed,
        }


def _pykokoro_discovery() -> Any:
    try:
        from pykokoro import discover_models
    except (ImportError, OSError) as exc:
        raise ModelDiscoveryError(f"PyKokoro model discovery is unavailable: {exc}") from exc
    return discover_models


def discover_model_info(
    *,
    language: str | None = None,
    status: str | None = None,
    offline: bool = False,
    refresh: bool = False,
) -> tuple[tuple[ModelInfo, ...], Any]:
    if offline and refresh:
        raise ModelDiscoveryError("--offline and --refresh cannot be combined")
    try:
        result = _pykokoro_discovery()(offline=offline, refresh=refresh)
    except Exception as exc:
        if isinstance(exc, ModelDiscoveryError):
            raise
        raise ModelDiscoveryError(str(exc)) from exc
    requested = normalize_language_key(language) if language else None
    models = tuple(ModelInfo.from_capabilities(item) for item in result.models)
    if requested is not None:
        models = tuple(item for item in models if language_matches(requested, item.languages))
    if status is not None:
        models = tuple(item for item in models if item.status == status)
    return models, result


def language_matches(requested: str, declared: tuple[str, ...]) -> bool:
    requested = normalize_language_key(requested)
    requested_base = requested.partition("-")[0]
    return any(
        requested == normalize_language_key(item)
        or requested_base == normalize_language_key(item).partition("-")[0]
        for item in declared
    )


def get_model_info(
    model_id: str,
    *,
    offline: bool = False,
    refresh: bool = False,
) -> tuple[ModelInfo, Any]:
    models, result = discover_model_info(offline=offline, refresh=refresh)
    for model in models:
        if model.id == model_id:
            return model, result
    raise ModelDiscoveryError(
        f"Unknown model '{model_id}'. Run `readio models list` to inspect available models."
    )


def validate_language_settings(
    language: str,
    settings: LanguageSettings,
    model: ModelInfo,
) -> LanguageSettings:
    """Validate a language profile against already-discovered model metadata."""
    normalized = normalize_language_key(language)
    if model.status != "ready":
        raise ModelDiscoveryError(f"Model '{model.id}' is not runnable: {model.status}")
    if not language_matches(normalized, model.languages):
        declared = ", ".join(model.languages) or "none"
        raise ModelDiscoveryError(
            f"Model '{model.id}' does not declare language '{normalized}'. Declared languages: {declared}"
        )
    if settings.source is not None and settings.source != model.source:
        raise ModelDiscoveryError(
            f"Model '{model.id}' is provided by source '{model.source}', not '{settings.source}'."
        )
    if settings.quality is not None and settings.quality not in model.qualities:
        available = ", ".join(model.qualities) or "none"
        raise ModelDiscoveryError(
            f"Quality '{settings.quality}' is not available for model '{model.id}'. Available qualities: {available}"
        )
    if settings.voice is not None and settings.voice not in model.voices:
        available = ", ".join(model.voices) or "none"
        raise ModelDiscoveryError(
            f"Voice '{settings.voice}' is not available for model '{model.id}'. Available voices: {available}"
        )
    if settings.lexicons is not None and model.lexicons is not None:
        missing = tuple(item for item in settings.lexicons if item not in model.lexicons)
        if missing:
            available = ", ".join(model.lexicons) or "none"
            raise ModelDiscoveryError(
                f"Lexicon '{missing[0]}' is not available for model '{model.id}' / language '{normalized}'. "
                f"Available lexicons: {available}"
            )
    if model.experimental and not settings.allow_experimental:
        raise ModelDiscoveryError(
            f"Model '{model.id}' uses an experimental frontend. "
            "Re-run with --allow-experimental to persist this default."
        )
    return settings


__all__ = [
    "ModelDiscoveryError",
    "ModelInfo",
    "discover_model_info",
    "get_model_info",
    "language_matches",
    "validate_language_settings",
]
