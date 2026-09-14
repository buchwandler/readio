"""Metadata-only voice catalog and selector resolution for Readio."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .config import normalize_language_key
from .models import ModelDiscoveryError, ModelInfo, discover_model_info

RUNNABLE_STATUSES = frozenset({"ready", "experimental"})
MODEL_PRIORITY = {"v1.0": 0, "v1.1-zh": 1}
_SELECTOR_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*-(\d+)$")


@dataclass(frozen=True, slots=True)
class VoiceCatalogEntry:
    selector: str
    number: int
    id: str
    gender: str
    language: str
    locale: str
    language_label: str
    model: str
    source: str
    default: bool
    status: str
    experimental: bool
    runtime_available: bool
    distribution_id: str | None = None
    provider: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "selector": self.selector,
            "number": self.number,
            "id": self.id,
            "gender": self.gender,
            "language": self.language,
            "locale": self.locale,
            "language_label": self.language_label,
            "model": self.model,
            "source": self.source,
            "default": self.default,
            "status": self.status,
            "experimental": self.experimental,
            "runtime_available": self.runtime_available,
            "distribution_id": self.distribution_id,
            "provider": self.provider,
        }


@dataclass(frozen=True, slots=True)
class VoiceCatalog:
    voices: tuple[VoiceCatalogEntry, ...]
    registry_source: str | None
    cache_fallback: bool
    offline: bool
    refreshed: bool


@dataclass(frozen=True, slots=True)
class VoiceSelectorResolution:
    requested: str
    selector: str | None
    language: str | None
    model: str | None
    source: str | None
    voice: str
    catalog_entry: VoiceCatalogEntry | None


def normalize_locale(value: str) -> str:
    return normalize_language_key(value)


def _fallback_metadata(model: ModelInfo, voice: str) -> tuple[str, str, str, str]:
    language = model.languages[0] if model.languages else "unknown"
    return "unknown", language, language, language


def _model_voice_metadata(model: ModelInfo, voice: str) -> tuple[str, str, str, str]:
    for detail in model.voice_details:
        if detail.id == voice:
            return detail.gender, detail.language, detail.locale, detail.language_label
    return _fallback_metadata(model, voice)


def _ordered_models(models: tuple[ModelInfo, ...]) -> tuple[ModelInfo, ...]:
    return tuple(sorted(models, key=lambda item: (MODEL_PRIORITY.get(item.id, 100), item.id, item.source)))


def build_voice_catalog(
    models: tuple[ModelInfo, ...],
    *,
    registry_source: str | None = None,
    cache_fallback: bool = False,
    offline: bool = False,
    refreshed: bool = False,
) -> VoiceCatalog:
    counters: dict[str, int] = {}
    entries: list[VoiceCatalogEntry] = []
    for model in _ordered_models(models):
        if model.status not in RUNNABLE_STATUSES or not model.runtime_available:
            continue
        for voice in model.voices:
            gender, language, locale, language_label = _model_voice_metadata(model, voice)
            locale_key = normalize_locale(locale)
            counters[locale_key] = counters.get(locale_key, 0) + 1
            number = counters[locale_key]
            entries.append(
                VoiceCatalogEntry(
                    selector=f"{locale_key}-{number}",
                    number=number,
                    id=voice,
                    gender=gender,
                    language=language,
                    locale=locale,
                    language_label=language_label,
                    model=model.id,
                    source=model.source,
                    default=voice == model.default_voice,
                    status=model.status,
                    experimental=model.experimental,
                    runtime_available=model.runtime_available,
                    distribution_id=model.distribution_id,
                    provider=model.provider,
                )
            )
    return VoiceCatalog(
        voices=tuple(entries),
        registry_source=registry_source,
        cache_fallback=cache_fallback,
        offline=offline,
        refreshed=refreshed,
    )


def discover_voice_catalog(
    *,
    offline: bool = False,
    refresh: bool = False,
    preference: str = "auto",
) -> tuple[tuple[VoiceCatalogEntry, ...], Any]:
    models, result = discover_model_info(offline=offline, refresh=refresh, preference=preference)
    catalog = build_voice_catalog(
        models,
        registry_source=getattr(result, "registry_source", None),
        cache_fallback=bool(getattr(result, "cache_fallback", False)),
        offline=bool(getattr(result, "offline", offline)),
        refreshed=bool(getattr(result, "refreshed", refresh)),
    )
    return catalog.voices, result


def _language_matches_entry(requested: str, entry: VoiceCatalogEntry) -> bool:
    requested = normalize_language_key(requested)
    locale = normalize_locale(entry.locale)
    language = normalize_locale(entry.language)
    if "-" in requested:
        return locale == requested or language == requested
    base = requested.partition("-")[0]
    return locale.partition("-")[0] == base or language.partition("-")[0] == base


def filter_voice_catalog(
    entries: tuple[VoiceCatalogEntry, ...],
    *,
    language: str | None = None,
    gender: str | None = None,
    model: str | None = None,
) -> tuple[VoiceCatalogEntry, ...]:
    return tuple(
        entry
        for entry in entries
        if (language is None or _language_matches_entry(language, entry))
        and (gender is None or entry.gender == gender)
        and (model is None or entry.model == model)
    )


def get_voice_selector(
    selector: str,
    *,
    offline: bool = False,
    refresh: bool = False,
    preference: str = "auto",
) -> tuple[VoiceCatalogEntry, Any]:
    entries, result = discover_voice_catalog(
        offline=offline, refresh=refresh, preference=preference
    )
    for entry in entries:
        if entry.selector == selector:
            return entry, result
    raise ModelDiscoveryError(
        f"Unknown voice selector {selector!r}. Run `readio voices list --lang {selector.rsplit('-', 1)[0]}`.",
        code="readio.voice_selector_not_found",
    )


def is_selector_shape(value: str) -> bool:
    return _SELECTOR_RE.fullmatch(value.strip().lower()) is not None


def _language_conflicts(requested: str, entry: VoiceCatalogEntry) -> bool:
    return not _language_matches_entry(requested, entry)


def resolve_voice_selector(
    voice: str | None,
    *,
    language: str | None,
    model: str | None,
    source: str | None,
    offline: bool = False,
    refresh: bool = False,
    preference: str = "auto",
) -> VoiceSelectorResolution | None:
    if voice is None:
        return None
    if not is_selector_shape(voice):
        return VoiceSelectorResolution(
            requested=voice,
            selector=None,
            language=language,
            model=model,
            source=source,
            voice=voice,
            catalog_entry=None,
        )
    entry, _ = get_voice_selector(
        voice.strip().lower(), offline=offline, refresh=refresh, preference=preference
    )
    if language is not None and _language_conflicts(language, entry):
        raise ModelDiscoveryError(
            f"Voice selector {voice!r} resolves to locale {entry.locale!r}, but language {language!r} was requested.",
            code="readio.voice_selector_language_conflict",
        )
    if model is not None and model != entry.model:
        raise ModelDiscoveryError(
            f"Voice selector {voice!r} belongs to model {entry.model!r}, but model {model!r} was requested.",
            code="readio.voice_selector_model_conflict",
        )
    if source is not None and source != entry.source:
        raise ModelDiscoveryError(
            f"Voice selector {voice!r} belongs to source {entry.source!r}, but source {source!r} was requested.",
            code="readio.voice_selector_source_conflict",
        )
    return VoiceSelectorResolution(
        requested=voice,
        selector=entry.selector,
        language=normalize_locale(entry.locale),
        model=entry.model,
        source=entry.source,
        voice=entry.id,
        catalog_entry=entry,
    )


def find_voice_entries(
    value: str,
    entries: tuple[VoiceCatalogEntry, ...],
) -> tuple[VoiceCatalogEntry, ...]:
    by_selector = tuple(entry for entry in entries if entry.selector == value)
    if by_selector:
        return by_selector
    return tuple(entry for entry in entries if entry.id == value)


__all__ = [
    "MODEL_PRIORITY",
    "RUNNABLE_STATUSES",
    "VoiceCatalog",
    "VoiceCatalogEntry",
    "VoiceSelectorResolution",
    "build_voice_catalog",
    "discover_voice_catalog",
    "filter_voice_catalog",
    "find_voice_entries",
    "get_voice_selector",
    "is_selector_shape",
    "normalize_locale",
    "resolve_voice_selector",
]
