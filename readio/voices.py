"""Metadata-only voice catalog and selector resolution for Readio."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .config import normalize_language_key
from .models import ModelDiscoveryError, ModelInfo, discover_model_info

RUNNABLE_STATUSES = frozenset({"ready", "experimental"})
MODEL_PRIORITY = {"v1.0": 0, "v1.1-zh": 1}
BACKEND_PRIORITY = {"pykokoro": 0, "pipersynth": 1}
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
    engine: str = "pykokoro"

    @property
    def backend(self) -> str:
        """Compatibility alias for the synthesis backend identity."""
        return self.engine

    @property
    def qualified_id(self) -> str:
        """Stable backend/model/voice identity, distinct from the short selector."""
        return f"{self.engine}:{self.model}:{self.id}"

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
            "engine": self.engine,
            "backend": self.engine,
            "qualified_id": self.qualified_id,
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
    engine: str | None = None


    @property
    def backend(self) -> str | None:
        return self.catalog_entry.engine if self.catalog_entry is not None else None

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
    return tuple(
        sorted(
            models,
            key=lambda item: (
                BACKEND_PRIORITY.get(item.backend, 100),
                MODEL_PRIORITY.get(item.id, 100),
                item.id,
                item.source,
            ),
        )
    )


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
                    engine=model.backend,
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
    engine: str | None = None,
 ) -> tuple[tuple[VoiceCatalogEntry, ...], Any]:
    models, result = discover_model_info(
        offline=offline, refresh=refresh, preference=preference, backend=engine
    )
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
    engine: str | None = None,
 ) -> tuple[VoiceCatalogEntry, ...]:
    return tuple(
        entry
        for entry in entries
        if (language is None or _language_matches_entry(language, entry))
        and (gender is None or entry.gender == gender)
        and (model is None or entry.model == model)
        and (engine is None or entry.engine == engine)
    )


def get_voice_selector(
    selector: str,
    *,
    offline: bool = False,
    refresh: bool = False,
    preference: str = "auto",
    engine: str | None = None,
) -> tuple[VoiceCatalogEntry, Any]:
    entries, result = discover_voice_catalog(
        offline=offline, refresh=refresh, preference=preference, engine=engine
    )
    matches = tuple(entry for entry in entries if entry.selector == selector)
    if len(matches) == 1:
        return matches[0], result
    if len(matches) > 1:
        alternatives = ", ".join(entry.qualified_id for entry in matches)
        raise ModelDiscoveryError(
            f"Voice selector {selector!r} is ambiguous across backends. "
            f"Use one of: {alternatives}",
            code="readio.voice_selector_ambiguous",
        )
    if engine is not None:
        all_entries, _ = discover_voice_catalog(
            offline=offline, refresh=refresh, preference=preference
        )
        candidates = tuple(item for item in all_entries if item.selector == selector)
        if candidates:
            actual = candidates[0].engine
            raise ModelDiscoveryError(
                f"Voice selector {selector!r} resolves to backend {actual!r}, but "
                f"backend {engine!r} was requested.",
                code="voice_selector_backend_conflict",
            )
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
    engine: str | None = None,
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
        voice.strip().lower(),
        offline=offline,
        refresh=refresh,
        preference=preference,
        engine=engine,
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
        engine=entry.engine,
        voice=entry.id,
        catalog_entry=entry,
    )


def find_voice_entries(
    value: str,
    entries: tuple[VoiceCatalogEntry, ...],
) -> tuple[VoiceCatalogEntry, ...]:
    qualified = tuple(entry for entry in entries if entry.qualified_id == value)
    if qualified:
        return qualified
    by_selector = tuple(entry for entry in entries if entry.selector == value)
    if by_selector:
        return by_selector
    return tuple(entry for entry in entries if entry.id == value)


__all__ = [
    "BACKEND_PRIORITY",
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
