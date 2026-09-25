"""Centralized effective synthesis option resolution."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace

from .config import (
    LanguageSettings,
    ReadioConfig,
    language_profile,
    normalize_language_key,
    normalize_short_sentence_policy,
    normalize_spacy_policy,
    normalize_voice_level,
)
from .models import ModelInfo, get_model_info, validate_language_settings
from .plan import SynthesisRequest
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
    backend: str = "pykokoro"
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
            backend=info.backend,
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
    voice_level: str
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
    engine: str = "pykokoro"


def _raw_synthesis_selection(
    cfg: ReadioConfig, request: SynthesisRequest
) -> tuple[str, LanguageSettings | None, bool]:
    cli_language = request.language
    language = normalize_language_key(cli_language or cfg.reader.lang)
    _, profile = language_profile(cfg, language)
    return language, profile, cli_language is not None


def _discovery_policy(request: SynthesisRequest, source: str | None) -> DiscoveryPolicy:
    return DiscoveryPolicy(
        offline=request.offline,
        refresh=request.refresh,
        preference=source or "auto",
    )


def _select_preferred_quality(qualities: tuple[str, ...]) -> str | None:
    if not qualities:
        return None
    return "fp32" if "fp32" in qualities else qualities[0]


def _legacy_synthesis_request(args: object | None) -> SynthesisRequest:
    if args is None:
        return SynthesisRequest()

    lexicons = getattr(args, "lexicons", None)
    detect_languages = getattr(args, "detect_languages", None)
    return SynthesisRequest(
        language=getattr(args, "lang", None),
        model=getattr(args, "model", None),
        model_source=getattr(args, "model_source", None),
        quality=getattr(args, "quality", None),
        voice=getattr(args, "voice", None),
        lexicons=tuple(lexicons) if lexicons is not None else None,
        speaker=getattr(args, "speaker", None),
        clear_lexicons=bool(getattr(args, "no_lexicons", False)),
        auto_lexicons=bool(getattr(args, "auto_lexicons", False)),
        spacy=getattr(args, "spacy", None),
        voice_level=getattr(args, "voice_level", None),
        short_sentence=getattr(args, "short_sentence", None),
        g2p_fallback=getattr(args, "g2p_fallback", None),
        lexicon_data_policy=getattr(args, "lexicon_data_policy", None),
        language_detection=getattr(args, "language_detection", None),
        detect_languages=tuple(detect_languages) if detect_languages is not None else None,
        allow_experimental=bool(getattr(args, "allow_experimental", False)),
        speed=getattr(args, "speed", None),
        pause_mode=getattr(args, "pause_mode", None),
        unit=getattr(args, "unit", None),
        offline=bool(getattr(args, "offline", False)),
        refresh=bool(getattr(args, "refresh", False)),
        engine=getattr(args, "engine", None),
    )


def resolve_synthesis(
    cfg: ReadioConfig, request: SynthesisRequest | object | None = None
) -> ResolvedSynthesis:
    """Resolve synthesis settings from a typed request or legacy CLI values."""
    if isinstance(request, SynthesisRequest):
        typed_request = request
    else:
        typed_request = _legacy_synthesis_request(request)
    return resolve_synthesis_request(cfg, typed_request)


def resolve_synthesis_request(cfg: ReadioConfig, request: SynthesisRequest) -> ResolvedSynthesis:
    """Resolve synthesis preferences without CLI-shaped state."""
    selector_resolution = resolve_voice_selector(
        request.voice,
        language=request.language,
        model=request.model,
        source=request.model_source,
        offline=request.offline,
        refresh=request.refresh,
        preference=request.model_source or "auto",
        engine=request.engine,
    )
    if selector_resolution is not None and selector_resolution.selector is not None:
        request = replace(
            request,
            language=selector_resolution.language,
            model=selector_resolution.model,
            model_source=selector_resolution.source,
            voice=selector_resolution.voice,
            engine=selector_resolution.backend,
        )
    language, profile, cli_language = _raw_synthesis_selection(cfg, request)

    logger.info(
        "synthesis.resolve language=%s model=%s",
        language,
        request.model or "profile/default",
    )
    model = profile.model if profile is not None else None
    source = profile.source if profile is not None else None
    quality = profile.quality if profile is not None else None
    voice = profile.voice if profile is not None else None
    lexicons = profile.lexicons if profile is not None else None
    engine = (
        profile.engine if profile is not None and profile.engine is not None else cfg.reader.engine
    )
    allow_experimental = profile.allow_experimental if profile is not None else False
    g2p_fallback = profile.g2p_fallback if profile is not None else None
    lexicon_data_policy = profile.lexicon_data_policy if profile is not None else None

    spacy = normalize_spacy_policy(request.spacy or cfg.reader.spacy)
    short_sentence = normalize_short_sentence_policy(
        request.short_sentence or cfg.reader.short_sentence
    )
    if request.model is not None:
        model = request.model
    if request.engine is not None:
        engine = request.engine
    from .engines.registry import get_engine, normalize_engine_id

    engine = normalize_engine_id(engine)
    get_engine(engine)
    raw_speed = request.speed if request.speed is not None else cfg.reader.speed
    if isinstance(raw_speed, bool):
        raise TypeError("synthesis.speed must be numeric")
    try:
        speed = float(raw_speed)
    except (TypeError, ValueError) as exc:
        raise ValueError("synthesis.speed must be finite and > 0") from exc
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError("synthesis.speed must be finite and > 0")
    if engine == "pocket" and speed != 1.0:
        raise ValueError(
            "synthesis.speed_unsupported: PocketSynth only supports synthesis speed 1.0"
        )
    voice_level = normalize_voice_level(
        request.voice_level if request.voice_level is not None else cfg.reader.voice_level
    )
    if request.model_source is not None:
        source = request.model_source
    if request.quality is not None:
        quality = request.quality
    if request.voice is not None:
        voice = request.voice
    if request.lexicons is not None:
        lexicons = tuple(request.lexicons)
    elif request.clear_lexicons:
        lexicons = ()
    elif request.auto_lexicons:
        lexicons = None
    if request.g2p_fallback is not None:
        g2p_fallback = request.g2p_fallback
    if request.lexicon_data_policy is not None:
        lexicon_data_policy = request.lexicon_data_policy
    language_detection = request.language_detection
    detect_languages = (
        request.detect_languages
        if request.detect_languages is not None
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
    if model is not None:
        policy = _discovery_policy(request, source)
        discovery_kwargs: dict[str, object] = {
            "offline": policy.offline,
            "refresh": policy.refresh,
            "preference": policy.preference,
        }
        if engine != "pykokoro":
            discovery_kwargs["engine"] = engine
        discovered, result = get_model_info(model, **discovery_kwargs)
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
                engine=engine,
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
        speed=speed,
        voice_level=voice_level,
        pause_mode=request.pause_mode or cfg.reader.pause_mode,
        unit=request.unit or cfg.reader.unit,
        resolved_model=resolved_model,
        model_voices=resolved_model.voices if resolved_model is not None else None,
        model_default_voice=resolved_model.default_voice if resolved_model is not None else None,
        discovery_source=discovery_source,
        discovery_cache_fallback=discovery_cache_fallback,
        discovery_offline=discovery_offline,
        discovery_refreshed=discovery_refreshed,
        engine=engine,
    )


__all__ = [
    "DiscoveryPolicy",
    "ResolvedModel",
    "ResolvedSynthesis",
    "resolve_synthesis",
    "resolve_synthesis_request",
]
