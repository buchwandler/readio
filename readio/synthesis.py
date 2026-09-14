"""Centralized effective synthesis option resolution."""

from __future__ import annotations

import logging
from argparse import Namespace
from dataclasses import dataclass

from .config import (
    LanguageSettings,
    ReadioConfig,
    language_profile,
    normalize_language_key,
    normalize_short_sentence_policy,
    normalize_spacy_policy,
)
from .models import ModelInfo, get_model_info, validate_language_settings
from .voices import resolve_voice_selector

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DiscoveryPolicy:
    offline: bool = False
    refresh: bool = False
    preference: str = "auto"


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    id: str
    source: str
    voices: tuple[str, ...]
    default_voice: str
    qualities: tuple[str, ...]
    lexicons: tuple[str, ...] | None
    status: str
    experimental: bool
    runtime_available: bool
    distribution_id: str | None = None
    provider: str | None = None
    sample_rate: int | None = None
    max_tokens: int | None = None

    @classmethod
    def from_info(cls, info: ModelInfo) -> ResolvedModel:
        return cls(
            id=info.id,
            source=info.source,
            voices=info.voices,
            default_voice=info.default_voice,
            qualities=info.qualities,
            lexicons=info.lexicons,
            status=info.status,
            experimental=info.experimental,
            runtime_available=info.runtime_available,
            distribution_id=info.distribution_id,
            provider=info.provider,
            sample_rate=info.sample_rate,
            max_tokens=info.max_tokens,
        )


@dataclass(frozen=True, slots=True)
class ResolvedSynthesis:
    language: str
    model: str | None
    source: str | None
    quality: str | None
    voice: str | None
    lexicons: tuple[str, ...] | None
    allow_experimental: bool
    speed: float
    pause_mode: str
    unit: str
    g2p_fallback: str | None = None
    spacy: str = "auto"
    short_sentence: str = "auto"
    lexicon_data_policy: str | None = None
    language_detection: str | None = None
    detect_languages: tuple[str, ...] | None = None
    resolved_model: ResolvedModel | None = None
    # Compatibility view for callers that only need the active concrete roster.
    model_voices: tuple[str, ...] | None = None
    model_default_voice: str | None = None
    discovery_source: str | None = None
    discovery_cache_fallback: bool = False
    discovery_offline: bool = False
    discovery_refreshed: bool = False


def _raw_synthesis_selection(
    cfg: ReadioConfig, args: Namespace
) -> tuple[str, LanguageSettings | None, bool]:
    cli_language = getattr(args, "lang", None)
    language = normalize_language_key(cli_language or cfg.reader.lang)
    _, profile = language_profile(cfg, language)
    return language, profile, cli_language is not None


def _discovery_policy(args: Namespace, source: str | None) -> DiscoveryPolicy:
    return DiscoveryPolicy(
        offline=bool(getattr(args, "offline", False)),
        refresh=bool(getattr(args, "refresh", False)),
        preference=source or "auto",
    )


def _select_preferred_quality(qualities: tuple[str, ...]) -> str | None:
    if not qualities:
        return None
    return "fp32" if "fp32" in qualities else qualities[0]


def resolve_synthesis(cfg: ReadioConfig, args: Namespace | None = None) -> ResolvedSynthesis:
    """Resolve and validate all reader, profile, and CLI synthesis settings once."""
    args = args or Namespace()
    selector_resolution = resolve_voice_selector(
        getattr(args, "voice", None),
        language=getattr(args, "lang", None),
        model=getattr(args, "model", None),
        source=getattr(args, "model_source", None),
        offline=bool(getattr(args, "offline", False)),
        refresh=bool(getattr(args, "refresh", False)),
        preference=getattr(args, "model_source", None) or "auto",
    )
    if selector_resolution is not None and selector_resolution.selector is not None:
        args = Namespace(**vars(args))
        args.lang = selector_resolution.language
        args.model = selector_resolution.model
        args.model_source = selector_resolution.source
        args.voice = selector_resolution.voice
    language, profile, cli_language = _raw_synthesis_selection(cfg, args)

    logger.info(
        "synthesis.resolve language=%s model=%s",
        language,
        getattr(args, "model", None) or "profile/default",
    )
    model = profile.model if profile is not None else None
    source = profile.source if profile is not None else None
    quality = profile.quality if profile is not None else None
    voice = profile.voice if profile is not None else None
    lexicons = profile.lexicons if profile is not None else None
    allow_experimental = profile.allow_experimental if profile is not None else False
    g2p_fallback = profile.g2p_fallback if profile is not None else None
    lexicon_data_policy = profile.lexicon_data_policy if profile is not None else None

    spacy = normalize_spacy_policy(getattr(args, "spacy", None) or cfg.reader.spacy)
    short_sentence = normalize_short_sentence_policy(
        getattr(args, "short_sentence", None) or cfg.reader.short_sentence
    )
    explicit_model = getattr(args, "model", None)
    if explicit_model is not None:
        model = explicit_model
    if getattr(args, "model_source", None) is not None:
        source = args.model_source
    if getattr(args, "quality", None) is not None:
        quality = args.quality
    if getattr(args, "voice", None) is not None:
        voice = args.voice
    if getattr(args, "lexicons", None) is not None:
        lexicons = tuple(args.lexicons)
    elif getattr(args, "no_lexicons", False):
        lexicons = ()
    elif getattr(args, "auto_lexicons", False):
        lexicons = None
    if getattr(args, "g2p_fallback", None) is not None:
        g2p_fallback = args.g2p_fallback
    if getattr(args, "lexicon_data_policy", None) is not None:
        lexicon_data_policy = args.lexicon_data_policy
    language_detection = getattr(args, "language_detection", None)
    detect_languages = (
        tuple(args.detect_languages)
        if getattr(args, "detect_languages", None) is not None
        else cfg.reader.detect_languages
    )
    if language_detection is None:
        language_detection = cfg.reader.language_detection
    if language_detection is None and detect_languages is not None:
        language_detection = "auto"

    resolved_model: ResolvedModel | None = None
    discovery_source: str | None = None
    discovery_cache_fallback = False
    discovery_offline = False
    discovery_refreshed = False
    # Any effective model, including one loaded from a persisted locale profile, follows
    # exactly the same public discovery and validation path as an explicit CLI model.
    if model is not None:
        policy = _discovery_policy(args, source)
        discovered, result = get_model_info(
            model,
            offline=policy.offline,
            refresh=policy.refresh,
            preference=policy.preference,
        )
        resolved_model = ResolvedModel.from_info(discovered)
        source = source or discovered.source
        if voice is None:
            voice = discovered.default_voice
        if quality is None:
            quality = _select_preferred_quality(discovered.qualities)
        validate_language_settings(
            language,
            LanguageSettings(
                model=model,
                source=source,
                quality=quality,
                voice=voice,
                lexicons=lexicons,
                g2p_fallback=g2p_fallback,
                lexicon_data_policy=lexicon_data_policy,
                allow_experimental=allow_experimental,
            ),
            discovered,
        )
        discovery_source = getattr(result, "registry_source", None)
        discovery_cache_fallback = bool(getattr(result, "cache_fallback", False))
        discovery_offline = bool(getattr(result, "offline", policy.offline))
        discovery_refreshed = bool(getattr(result, "refreshed", policy.refresh))

    # A global reader voice is a compatibility default only for the unchanged reader
    # domain. Language/profile/model overrides must leave voice open for PyKokoro.
    if voice is None and model is None and not cli_language and profile is None:
        voice = cfg.reader.voice

    logger.info(
        "synthesis.resolved language=%s model=%s source=%s quality=%s voice=%s",
        language,
        model,
        source or "default",
        quality or "default",
        voice or "default",
    )
    return ResolvedSynthesis(
        language=language,
        model=model,
        source=source,
        quality=quality,
        voice=voice,
        lexicons=lexicons,
        g2p_fallback=g2p_fallback,
        lexicon_data_policy=lexicon_data_policy,
        spacy=spacy,
        short_sentence=short_sentence,
        language_detection=language_detection,
        detect_languages=detect_languages,
        allow_experimental=allow_experimental,
        speed=float(
            getattr(args, "speed", None)
            if getattr(args, "speed", None) is not None
            else cfg.reader.speed
        ),
        pause_mode=(
            getattr(args, "pause_mode", None)
            if getattr(args, "pause_mode", None) is not None
            else cfg.reader.pause_mode
        ),
        unit=(
            getattr(args, "unit", None)
            if getattr(args, "unit", None) is not None
            else cfg.reader.unit
        ),
        resolved_model=resolved_model,
        model_voices=resolved_model.voices if resolved_model is not None else None,
        model_default_voice=resolved_model.default_voice if resolved_model is not None else None,
        discovery_source=discovery_source,
        discovery_cache_fallback=discovery_cache_fallback,
        discovery_offline=discovery_offline,
        discovery_refreshed=discovery_refreshed,
    )


__all__ = [
    "DiscoveryPolicy",
    "ResolvedModel",
    "ResolvedSynthesis",
    "resolve_synthesis",
]
