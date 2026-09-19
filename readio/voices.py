"""Metadata-only voice catalog and stable selector resolution for Readio."""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from typing import Any

from onnxvoice import is_voice_selector, parse_voice_selector, selector_for_voice
from onnxvoice import resolve_voice_selector as onnxvoice_resolve_voice_selector

from .config import normalize_language_key
from .engines.catalog import CatalogResult
from .engines.discovery import discover_targets
from .engines.registry import (
    ONNXVOICE_SYSTEM_TO_READIO_ENGINE,
    READIO_ENGINE_TO_ONNXVOICE_SYSTEM,
    normalize_engine_id,
)
from .models import ModelDiscoveryError, ModelInfo, discover_model_info

RUNNABLE_STATUSES = frozenset({"ready", "experimental"})

# These priorities are display ordering only. They must never participate in selector identity.
MODEL_PRIORITY = {"v1.0": 0, "v1.1-zh": 1}
BACKEND_PRIORITY = {"pykokoro": 0, "pipersynth": 1}
_LEGACY_KOKORO_SELECTOR_RE = re.compile(
    r"^(?P<language>[a-z0-9]+(?:[-_][a-z0-9]+)*)-(?P<slot>[1-9][0-9]*)$",
    re.IGNORECASE,
)
_LEGACY_LOCALE_CODES = frozenset(
    {
        "at",
        "au",
        "br",
        "ca",
        "ch",
        "cn",
        "de",
        "es",
        "fr",
        "gb",
        "in",
        "it",
        "jp",
        "kr",
        "pt",
        "ru",
        "us",
    }
)


@dataclass(frozen=True, slots=True)
class VoiceCatalogEntry:
    selector: str | None
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
    slot: int | None = None
    selector_language: str | None = None
    selector_engine_code: str | None = None
    # Compatibility-only constructor/property for one release. Use slot instead.
    number: int | None = None

    def __post_init__(self) -> None:
        if self.slot is None and self.number is not None:
            object.__setattr__(self, "slot", self.number)
        elif self.number is None and self.slot is not None:
            object.__setattr__(self, "number", self.slot)

    @property
    def backend(self) -> str:
        """Compatibility alias for the synthesis backend identity."""
        return self.engine

    @property
    def qualified_id(self) -> str:
        """Stable backend/model/voice identity, distinct from the short selector."""
        return f"{self.engine}:{self.model}:{self.id}"

    @property
    def selector_status(self) -> str:
        return "assigned" if self.selector is not None else "unassigned"

    def to_dict(self) -> dict[str, Any]:
        return {
            "selector": self.selector,
            "slot": self.slot,
            # Compatibility-only field; callers should migrate to slot.
            "number": self.number,
            "selector_language": self.selector_language,
            "selector_engine_code": self.selector_engine_code,
            "selector_status": self.selector_status,
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
        return self.catalog_entry.engine if self.catalog_entry is not None else self.engine


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
    """Order models for presentation only; this is not selector identity."""
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


def _identity_for_kokoro(model: ModelInfo, voice: str) -> Any | None:
    if model.backend != "pykokoro":
        return None
    system = READIO_ENGINE_TO_ONNXVOICE_SYSTEM[model.backend]
    # Prefer the canonical model ID; distribution_id is a compatibility fallback for
    # discovery providers that expose a package alias.
    candidates = tuple(dict.fromkeys(item for item in (model.id, model.distribution_id) if item))
    for asset_id in candidates:
        identity = selector_for_voice(system=system, asset_id=asset_id, voice_id=voice)
        if identity is not None:
            return identity
    return None


def _identity_for_piper(target: Any) -> Any | None:
    system = READIO_ENGINE_TO_ONNXVOICE_SYSTEM["piper"]
    return selector_for_voice(system=system, asset_id=target.id, voice_id=target.id)


def _entry_selector_fields(
    identity: Any | None,
) -> tuple[str | None, int | None, str | None, str | None]:
    if identity is None:
        return None, None, None, None
    return identity.selector, identity.slot, identity.language, identity.engine_code


def build_voice_catalog(
    models: tuple[ModelInfo, ...],
    *,
    registry_source: str | None = None,
    cache_fallback: bool = False,
    offline: bool = False,
    refreshed: bool = False,
) -> VoiceCatalog:
    entries: list[VoiceCatalogEntry] = []
    for model in _ordered_models(models):
        if model.status not in RUNNABLE_STATUSES or not model.runtime_available:
            continue
        for voice in model.voices:
            gender, language, locale, language_label = _model_voice_metadata(model, voice)
            identity = _identity_for_kokoro(model, voice)
            selector, slot, selector_language, selector_engine_code = _entry_selector_fields(
                identity
            )
            entries.append(
                VoiceCatalogEntry(
                    selector=selector,
                    slot=slot,
                    selector_language=selector_language,
                    selector_engine_code=selector_engine_code,
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


def _piper_voice_catalog(
    *,
    offline: bool,
    refresh: bool,
    language: str | None,
    engine: str,
) -> tuple[tuple[VoiceCatalogEntry, ...], CatalogResult]:
    """Project Piper targets while consuming OnnxVoice selector identities."""
    result = discover_targets(
        engine=engine,
        language=language,
        offline=offline,
        refresh=refresh,
    )
    entries = []
    for target in result.targets:
        identity = _identity_for_piper(target)
        selector, slot, selector_language, selector_engine_code = _entry_selector_fields(identity)
        target_language = target.languages[0] if target.languages else "unknown"
        entries.append(
            VoiceCatalogEntry(
                selector=selector,
                slot=slot,
                selector_language=selector_language,
                selector_engine_code=selector_engine_code,
                id=target.id,
                gender="unknown",
                language=target_language,
                locale=target_language,
                language_label=str(
                    target.metadata.get("region")
                    or target.metadata.get("language_family")
                    or "unknown"
                ),
                model=target.id,
                source="pipersynth",
                default=False,
                status=target.status,
                experimental=target.status == "experimental",
                runtime_available=target.runtime_available,
                distribution_id=target.id,
                provider="piper",
                engine="piper",
            )
        )
    return tuple(entries), result


def discover_voice_catalog(
    *,
    offline: bool = False,
    refresh: bool = False,
    preference: str = "auto",
    engine: str | None = None,
    language: str | None = None,
) -> tuple[tuple[VoiceCatalogEntry, ...], Any]:
    canonical_engine = normalize_engine_id(engine) if engine is not None else None
    if canonical_engine == "piper":
        return _piper_voice_catalog(
            offline=offline,
            refresh=refresh,
            language=language,
            engine=canonical_engine,
        )
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


def is_legacy_kokoro_selector(value: str) -> bool:
    """Return whether *value* uses Readio's old engine-less Kokoro shape."""
    if not isinstance(value, str) or _is_registered_stable_selector(value):
        return False
    return _legacy_selector_to_canonical(value) is not None


def _is_registered_stable_selector(value: str) -> bool:
    if not isinstance(value, str) or not is_voice_selector(value):
        return False
    try:
        _language, engine_code, _slot = value.strip().lower().rsplit("-", 2)
    except ValueError:
        return False
    return engine_code in {"ko", "pi"}


def _legacy_selector_to_canonical(value: str) -> str | None:
    if not isinstance(value, str) or _is_registered_stable_selector(value):
        return None
    match = _LEGACY_KOKORO_SELECTOR_RE.fullmatch(value.strip().lower())
    if match is None:
        return None
    if is_voice_selector(value):
        try:
            _language, engine_code, _slot = value.strip().lower().rsplit("-", 2)
        except ValueError:
            return None
        if engine_code not in _LEGACY_LOCALE_CODES:
            return None
    language = match.group("language").replace("-", "_")
    return f"{language}-ko-{match.group('slot')}"


def _readio_engine_for_system(system: str) -> str:
    try:
        return ONNXVOICE_SYSTEM_TO_READIO_ENGINE[system]
    except KeyError as exc:
        raise ModelDiscoveryError(
            f"OnnxVoice selector system {system!r} is not supported by Readio.",
            code="readio.voice_selector_system_unsupported",
        ) from exc


def _onnxvoice_identity(selector: str) -> Any:
    try:
        parsed = parse_voice_selector(selector)
    except Exception as exc:
        raise ModelDiscoveryError(
            f"Invalid stable voice selector {selector!r}: {exc}",
            code="readio.voice_selector_invalid",
        ) from exc
    if parsed.engine_code not in {"ko", "pi"}:
        raise ModelDiscoveryError(
            f"Voice selector {selector!r} uses unknown engine code {parsed.engine_code!r}.",
            code="readio.voice_selector_engine_unknown",
        )
    try:
        return onnxvoice_resolve_voice_selector(selector)
    except Exception as exc:  # library errors intentionally become Readio diagnostics
        raise ModelDiscoveryError(
            f"Unknown stable voice selector {selector!r}: {exc}",
            code="readio.voice_selector_not_found",
        ) from exc


def get_voice_selector(
    selector: str,
    *,
    offline: bool = False,
    refresh: bool = False,
    preference: str = "auto",
    engine: str | None = None,
) -> tuple[VoiceCatalogEntry, Any]:
    requested = selector.strip().lower()
    canonical_selector = _legacy_selector_to_canonical(requested) or requested
    identity = _onnxvoice_identity(canonical_selector)
    readio_engine = _readio_engine_for_system(identity.system)
    requested_engine = normalize_engine_id(engine) if engine is not None else None
    if requested_engine is not None and requested_engine != readio_engine:
        raise ModelDiscoveryError(
            f"Voice selector {canonical_selector!r} resolves to {readio_engine!r} voice "
            f"{identity.voice_id!r}, but engine {requested_engine!r} was requested.",
            code="voice_selector_engine_conflict",
        )
    entries, result = discover_voice_catalog(
        offline=offline,
        refresh=refresh,
        preference=preference,
        engine=readio_engine,
    )
    matches = tuple(
        entry
        for entry in entries
        if (
            entry.engine == readio_engine
            and entry.model == identity.asset_id
            and entry.id == identity.voice_id
        )
    )
    if not matches:
        # A test/future catalog may already project the stable selector while its
        # canonical model metadata is temporarily unavailable.
        matches = tuple(entry for entry in entries if entry.selector == identity.selector)
    if len(matches) == 1:
        return matches[0], result
    if len(matches) > 1:
        alternatives = ", ".join(entry.qualified_id for entry in matches)
        raise ModelDiscoveryError(
            f"Voice selector {canonical_selector!r} is ambiguous. Use one of: {alternatives}",
            code="readio.voice_selector_ambiguous",
        )
    raise ModelDiscoveryError(
        f"Voice selector {canonical_selector!r} resolves to {readio_engine} voice "
        f"{identity.voice_id!r}, but that identity is not present in the current catalog.",
        code="readio.voice_selector_not_found",
    )


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

    requested = voice.strip()
    canonical_selector = requested.lower()
    legacy = is_legacy_kokoro_selector(canonical_selector)
    if legacy:
        canonical_selector = _legacy_selector_to_canonical(canonical_selector) or canonical_selector
        warnings.warn(
            f"Voice selector {requested!r} is deprecated; use {canonical_selector!r}.",
            UserWarning,
            stacklevel=2,
        )
    elif not is_voice_selector(canonical_selector):
        return VoiceSelectorResolution(
            requested=voice,
            selector=None,
            language=language,
            model=model,
            source=source,
            voice=voice,
            catalog_entry=None,
            engine=normalize_engine_id(engine) if engine is not None else None,
        )

    entry, _ = get_voice_selector(
        canonical_selector,
        offline=offline,
        refresh=refresh,
        preference=preference,
        engine=engine,
    )
    readio_engine = entry.engine
    if language is not None and _language_conflicts(language, entry):
        raise ModelDiscoveryError(
            f"Voice selector {canonical_selector!r} resolves to locale {entry.locale!r}, "
            f"but language {language!r} was requested.",
            code="readio.voice_selector_language_conflict",
        )
    if model is not None and model != entry.model:
        raise ModelDiscoveryError(
            f"Voice selector {canonical_selector!r} belongs to model {entry.model!r}, "
            f"but model {model!r} was requested.",
            code="readio.voice_selector_model_conflict",
        )
    if source is not None and source != entry.source:
        raise ModelDiscoveryError(
            f"Voice selector {canonical_selector!r} belongs to source {entry.source!r}, "
            f"but source {source!r} was requested.",
            code="readio.voice_selector_source_conflict",
        )
    return VoiceSelectorResolution(
        requested=voice,
        selector=entry.selector,
        language=normalize_locale(entry.locale),
        model=entry.model,
        source=entry.source,
        engine=readio_engine,
        voice=entry.id,
        catalog_entry=entry,
    )


def find_voice_entries(
    value: str,
    entries: tuple[VoiceCatalogEntry, ...],
) -> tuple[VoiceCatalogEntry, ...]:
    canonical_value = _legacy_selector_to_canonical(value) or value
    if _legacy_selector_to_canonical(value) is not None:
        canonical_legacy = _legacy_selector_to_canonical(value)
        warnings.warn(
            f"Voice selector {value!r} is deprecated; use {canonical_legacy!r}.",
            UserWarning,
            stacklevel=2,
        )
    qualified = tuple(entry for entry in entries if entry.qualified_id == value)
    if qualified:
        return qualified
    by_selector = tuple(entry for entry in entries if entry.selector == canonical_value)
    if by_selector:
        return by_selector
    return tuple(entry for entry in entries if entry.id == value)


def is_selector_shape(value: str) -> bool:
    """Compatibility alias for stable OnnxVoice selector syntax only."""
    return is_voice_selector(value)


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
    "is_legacy_kokoro_selector",
    "is_selector_shape",
    "normalize_locale",
    "resolve_voice_selector",
]
