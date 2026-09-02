"""Centralized effective synthesis option resolution."""

from __future__ import annotations

from argparse import Namespace
from dataclasses import dataclass

from .config import ReadioConfig, language_profile, normalize_language_key


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


def resolve_synthesis(cfg: ReadioConfig, args: Namespace | None = None) -> ResolvedSynthesis:
    """Resolve reader, language-profile, and explicit CLI synthesis settings."""
    args = args or Namespace()
    cli_language = getattr(args, "lang", None)
    language = normalize_language_key(cli_language or cfg.reader.lang)
    _, profile = language_profile(cfg, language)

    model = profile.model if profile is not None else None
    source = profile.source if profile is not None else None
    quality = profile.quality if profile is not None else None
    voice = profile.voice if profile is not None else None
    lexicons = profile.lexicons if profile is not None else None
    allow_experimental = profile.allow_experimental if profile is not None else False

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
        lexicons = None
    allow_experimental = allow_experimental or bool(getattr(args, "allow_experimental", False))

    # Explicit model selection is resolved and validated before rendering.
    needs_discovery = explicit_model is not None
    if needs_discovery:
        from .config import LanguageSettings
        from .models import get_model_info, validate_language_settings

        discovered, _ = get_model_info(
            model,
            offline=bool(getattr(args, "offline", False)),
            refresh=bool(getattr(args, "refresh", False)),
        )
        source = source or discovered.source
        if voice is None:
            voice = discovered.default_voice
        if quality is None and discovered.qualities:
            quality = "fp32" if "fp32" in discovered.qualities else discovered.qualities[0]
        validate_language_settings(
            language,
            LanguageSettings(
                model=model,
                source=source,
                quality=quality,
                voice=voice,
                lexicons=lexicons,
                allow_experimental=allow_experimental,
            ),
            discovered,
        )

    if voice is None:
        voice = cfg.reader.voice
    return ResolvedSynthesis(
        language=language,
        model=model,
        source=source,
        quality=quality,
        voice=voice,
        lexicons=lexicons,
        allow_experimental=allow_experimental,
        speed=float(getattr(args, "speed", None) or cfg.reader.speed),
        pause_mode=getattr(args, "pause_mode", None) or cfg.reader.pause_mode,
        unit=getattr(args, "unit", None) or cfg.reader.unit,
    )


__all__ = ["ResolvedSynthesis", "resolve_synthesis"]
