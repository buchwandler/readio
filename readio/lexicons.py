"""Backend-neutral discovery and filtering of named synthesis lexicons."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .backends import get_backend, iter_backends
from .backends.base import DiscoveryInfo
from .config import normalize_language_key

_BACKEND_PRIORITY = {"pykokoro": 0, "pipersynth": 1}


@dataclass(frozen=True, slots=True)
class LexiconCatalogEntry:
    """A named lexicon selector and its optional data metadata."""

    selector: str
    engine: str
    language: str
    locale: str
    asset_id: str | None
    data_backend: str | None
    default: bool
    installed: bool | None
    models: tuple[str, ...] = ()
    model_support: str = "unknown"
    display_name: str | None = None
    phoneme_encoding: str | None = None
    data_version: str | None = None

    @property
    def backend(self) -> str:
        return self.engine

    def to_dict(self) -> dict[str, Any]:
        return {
            "selector": self.selector,
            "engine": self.engine,
            "backend": self.engine,
            "language": self.language,
            "locale": self.locale,
            "asset_id": self.asset_id,
            "data_backend": self.data_backend,
            "display_name": self.display_name,
            "phoneme_encoding": self.phoneme_encoding,
            "data_version": self.data_version,
            "default": self.default,
            "installed": self.installed,
            "models": list(self.models),
            "model_support": self.model_support,
        }


def _language_matches(requested: str, entry: LexiconCatalogEntry) -> bool:
    requested = normalize_language_key(requested)
    locale = normalize_language_key(entry.locale)
    if "-" in requested:
        return locale == requested
    return locale.partition("-")[0] == requested


def _entry_key(entry: LexiconCatalogEntry) -> tuple[object, ...]:
    return (entry.engine, normalize_language_key(entry.locale), entry.selector, entry.asset_id or "")


def discover_lexicon_catalog(
    *,
    language: str | None = None,
    model: str | None = None,
    engine: str | None = None,
    offline: bool = False,
    refresh: bool = False,
    preference: str = "auto",
) -> tuple[tuple[LexiconCatalogEntry, ...], DiscoveryInfo]:
    """Aggregate named lexicons from the selected registered backends."""

    backends = (get_backend(engine),) if engine is not None else tuple(iter_backends())
    entries: list[LexiconCatalogEntry] = []
    provenance: list[DiscoveryInfo] = []
    for backend in backends:
        discovered, info = backend.discover_lexicons(
            language=language,
            model=model,
            offline=offline,
            refresh=refresh,
            preference=preference,
        )
        entries.extend(discovered)
        provenance.append(info)

    entries.sort(key=lambda entry: (_BACKEND_PRIORITY.get(entry.engine, 100), *_entry_key(entry)))
    return tuple(entries), DiscoveryInfo(
        registry_source=", ".join(
            sorted({info.registry_source for info in provenance if info.registry_source})
        )
        or None,
        cache_fallback=any(info.cache_fallback for info in provenance),
        offline=all(info.offline for info in provenance) if provenance else offline,
        refreshed=any(info.refreshed for info in provenance),
    )


def filter_lexicon_catalog(
    entries: tuple[LexiconCatalogEntry, ...],
    *,
    language: str | None = None,
    model: str | None = None,
    engine: str | None = None,
    selector: str | None = None,
) -> tuple[LexiconCatalogEntry, ...]:
    """Filter catalog entries without collapsing locale or backend duplicates."""

    return tuple(
        entry
        for entry in entries
        if (language is None or _language_matches(language, entry))
        and (model is None or entry.model_support == "unknown" or model in entry.models)
        and (engine is None or entry.engine == engine)
        and (selector is None or entry.selector == selector)
    )


def find_lexicon_entries(
    value: str,
    entries: tuple[LexiconCatalogEntry, ...],
) -> tuple[LexiconCatalogEntry, ...]:
    """Find entries by selector or exact underlying asset ID."""

    selectors = tuple(entry for entry in entries if entry.selector == value)
    if selectors:
        return selectors
    return tuple(entry for entry in entries if entry.asset_id == value)


__all__ = [
    "LexiconCatalogEntry",
    "discover_lexicon_catalog",
    "filter_lexicon_catalog",
    "find_lexicon_entries",
]
