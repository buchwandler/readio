"""Metadata-only voice catalog and semantic voice reference resolution for Readio."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .catalog_metadata import language_tags_match
from .config import normalize_language_key
from .engines.discovery import discover_targets
from .engines.registry import get_engine, normalize_engine_id
from .models import ModelDiscoveryError, ModelInfo
from .voice_refs import (
    VoiceRef,
    engine_for_public_system,
    is_voice_ref,
    parse_voice_ref,
    public_system_for_engine,
)

RUNNABLE_STATUSES = frozenset({"ready", "experimental"})

# These priorities are presentation ordering only and never participate in voice identity.
MODEL_PRIORITY = {"v1.0": 0, "v1.1-zh": 1}
ENGINE_PRIORITY = {"kokoro": 0, "piper": 1, "pocket": 2, "kitten": 3}


@dataclass(frozen=True, slots=True)
class VoiceCatalogEntry:
    ref: str | None
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
    engine: str = "kokoro"

    languages: tuple[str, ...] = ()

    @property
    def target_id(self) -> str:
        return self.model

    @property
    def qualified_id(self) -> str:
        """Structured engine, target, and voice identity."""
        return f"{self.engine}:{self.target_id}:{self.id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "id": self.id,
            "gender": self.gender,
            "language": self.language,
            "locale": self.locale,
            "language_label": self.language_label,
            "model": self.model,
            "target_id": self.target_id,
            "source": self.source,
            "default": self.default,
            "status": self.status,
            "experimental": self.experimental,
            "runtime_available": self.runtime_available,
            "distribution_id": self.distribution_id,
            "provider": self.provider,
            "engine": self.engine,
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
class VoiceResolution:
    requested: str
    ref: str | None
    language: str | None
    target_id: str
    source: str
    voice: str
    catalog_entry: VoiceCatalogEntry
    engine: str

    @property
    def model(self) -> str:
        return self.target_id


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
    """Order models for presentation only; order does not determine voice identity."""
    return tuple(
        sorted(
            models,
            key=lambda item: (
                ENGINE_PRIORITY.get(item.engine, 100),
                MODEL_PRIORITY.get(item.id, 100),
                item.id,
                item.source,
            ),
        )
    )


def _voice_ref_for(engine: str, target_id: str, voice_id: str) -> str | None:
    try:
        system = public_system_for_engine(engine)
    except ValueError:
        return None
    child_voice = None if system == "piper" else voice_id
    return VoiceRef(system=system, target_id=target_id, voice_id=child_voice).value


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
            entries.append(
                VoiceCatalogEntry(
                    ref=_voice_ref_for(model.engine, model.id, voice),
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
                    engine=model.engine,
                )
            )
    return VoiceCatalog(
        voices=tuple(entries),
        registry_source=registry_source,
        cache_fallback=cache_fallback,
        offline=offline,
        refreshed=refreshed,
    )


def _target_voice_entries(targets: tuple[Any, ...]) -> tuple[VoiceCatalogEntry, ...]:
    entries: list[VoiceCatalogEntry] = []
    for target in targets:
        if target.status not in RUNNABLE_STATUSES or not target.runtime_available:
            continue
        try:
            system = public_system_for_engine(target.engine)
        except ValueError:
            system = None
        indexed = {detail.id: detail for detail in target.voice_details}
        metadata = target.metadata
        voices = (target.id,) if system == "piper" else tuple(target.voices)
        default_voice = target.default_voice
        fallback = target.languages[0] if target.languages else "unknown"
        for voice in voices:
            detail = indexed.get(voice)
            entries.append(
                VoiceCatalogEntry(
                    ref=_voice_ref_for(target.engine, target.id, voice),
                    id=voice,
                    gender=(detail.gender if detail else None)
                    or metadata.get("gender")
                    or "unknown",
                    language=(detail.language if detail else None)
                    or metadata.get("language")
                    or fallback,
                    locale=(detail.locale if detail else None)
                    or metadata.get("locale")
                    or fallback,
                    language_label=(detail.language_label if detail else None)
                    or metadata.get("language_label")
                    or fallback,
                    model=target.id,
                    source=str(metadata.get("source") or target.engine),
                    default=voice == default_voice,
                    status=target.status,
                    experimental=target.status == "experimental",
                    runtime_available=target.runtime_available,
                    distribution_id=str(metadata.get("distribution_id") or target.id),
                    provider=(str(metadata["provider"]) if metadata.get("provider") else None),
                    engine=target.engine,
                    languages=(tuple(detail.languages) if detail else ()),
                )
            )
    return tuple(entries)


def discover_voice_catalog(
    *,
    offline: bool = False,
    refresh: bool = False,
    preference: str = "auto",
    engine: str | None = None,
    language: str | None = None,
) -> tuple[tuple[VoiceCatalogEntry, ...], Any]:
    result = discover_targets(
        engine=engine,
        language=language,
        offline=offline,
        refresh=refresh,
        preference=preference,
    )
    return _target_voice_entries(result.targets), result


def _language_matches_entry(requested: str, entry: VoiceCatalogEntry) -> bool:
    requested = normalize_language_key(requested)
    if entry.languages:
        return any(language_tags_match(requested, available) for available in entry.languages)
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


def _raise_resolution_error(message: str, code: str) -> ModelDiscoveryError:
    return ModelDiscoveryError(message, code=code)


def resolve_voice_reference(
    voice: str | None,
    *,
    language: str | None,
    model: str | None,
    source: str | None,
    offline: bool = False,
    refresh: bool = False,
    preference: str = "auto",
    engine: str | None = None,
) -> VoiceResolution | None:
    """Resolve a semantic reference or an unambiguous native voice ID."""
    if voice is None:
        return None
    requested = voice.strip()
    requested_engine = normalize_engine_id(engine.strip()) if engine is not None else None
    reference: VoiceRef | None = None
    if ":" in requested:
        try:
            reference = parse_voice_ref(requested)
        except (TypeError, ValueError) as exc:
            raise _raise_resolution_error(
                f"Invalid voice reference {requested!r}: {exc}",
                "readio.voice_reference_invalid",
            ) from exc

    if reference is not None:
        resolved_engine = engine_for_public_system(reference.system)
        if requested_engine is not None and requested_engine != resolved_engine:
            raise _raise_resolution_error(
                f"Voice reference {reference.value!r} resolves to engine {resolved_engine!r}, "
                f"but engine {requested_engine!r} was requested.",
                "readio.voice_reference_engine_conflict",
            )
        if model is not None and model != reference.target_id:
            raise _raise_resolution_error(
                f"Voice reference {reference.value!r} targets {reference.target_id!r}, "
                f"but target {model!r} was requested.",
                "readio.voice_reference_target_conflict",
            )
        entries, _result = discover_voice_catalog(
            offline=offline,
            refresh=refresh,
            preference=preference,
            engine=resolved_engine,
        )
        matches = tuple(
            entry
            for entry in entries
            if entry.target_id == reference.target_id
            and (reference.voice_id is None or entry.id == reference.voice_id)
        )
    else:
        if requested_engine is not None:
            try:
                public_system_for_engine(requested_engine)
            except ValueError:
                return None
            adapter = get_engine(requested_engine)
            if not callable(getattr(adapter, "discover", None)):
                return None
        if requested_engine == "pocket" and model is None:
            return None
        entries, _result = discover_voice_catalog(
            offline=offline,
            refresh=refresh,
            preference=preference,
            engine=requested_engine,
        )
        matches = tuple(
            entry
            for entry in entries
            if (entry.id == requested or entry.qualified_id == requested)
            and (model is None or entry.target_id == model)
            and (source is None or entry.source == source)
        )

    if not matches:
        if reference is not None:
            raise _raise_resolution_error(
                f"Voice reference {reference.value!r} does not identify a voice in the current catalog.",
                "readio.voice_reference_not_found",
            )
        raise _raise_resolution_error(
            f"Native voice ID {requested!r} did not match a voice in the current catalog. "
            "Specify a semantic reference or enough engine and target context.",
            "readio.voice_reference_not_found",
        )
    if len(matches) > 1:
        alternatives = ", ".join(entry.ref or entry.qualified_id for entry in matches)
        raise _raise_resolution_error(
            f"Voice {requested!r} is ambiguous. Use one of: {alternatives}.",
            "readio.voice_reference_ambiguous",
        )

    entry = matches[0]
    if language is not None and not _language_matches_entry(language, entry):
        raise _raise_resolution_error(
            f"Voice {entry.ref!r} has locale {entry.locale!r}, "
            f"which conflicts with requested language {language!r}.",
            "readio.voice_reference_language_conflict",
        )
    if model is not None and model != entry.target_id:
        raise _raise_resolution_error(
            f"Voice {entry.ref!r} belongs to target {entry.target_id!r}, "
            f"but target {model!r} was requested.",
            "readio.voice_reference_target_conflict",
        )
    if source is not None and source != entry.source:
        raise _raise_resolution_error(
            f"Voice {entry.ref!r} belongs to source {entry.source!r}, "
            f"but source {source!r} was requested.",
            "readio.voice_reference_source_conflict",
        )
    return VoiceResolution(
        requested=voice,
        ref=entry.ref,
        language=(normalize_locale(entry.locale) if entry.locale != "unknown" else None),
        target_id=entry.target_id,
        source=entry.source,
        engine=entry.engine,
        voice=entry.id,
        catalog_entry=entry,
    )


def find_voice_entries(
    value: str,
    entries: tuple[VoiceCatalogEntry, ...],
) -> tuple[VoiceCatalogEntry, ...]:
    """Find catalog entries by semantic reference, structured ID, or native ID."""
    if is_voice_ref(value):
        reference = parse_voice_ref(value)
        engine = engine_for_public_system(reference.system)
        return tuple(
            entry
            for entry in entries
            if entry.engine == engine
            and entry.target_id == reference.target_id
            and (reference.voice_id is None or entry.id == reference.voice_id)
        )
    qualified = tuple(entry for entry in entries if entry.qualified_id == value)
    if qualified:
        return qualified
    return tuple(entry for entry in entries if entry.id == value)


__all__ = [
    "ENGINE_PRIORITY",
    "MODEL_PRIORITY",
    "RUNNABLE_STATUSES",
    "VoiceCatalog",
    "VoiceCatalogEntry",
    "VoiceResolution",
    "build_voice_catalog",
    "discover_voice_catalog",
    "filter_voice_catalog",
    "find_voice_entries",
    "normalize_locale",
    "resolve_voice_reference",
]
