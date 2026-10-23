"""Generic VoiceRender selector normalization shared by speech and profile APIs."""

from __future__ import annotations

from dataclasses import dataclass

import voicerender  # type: ignore[import-not-found]


@dataclass(frozen=True, slots=True)
class VoiceSelector:
    engine: str | None = None
    target_id: str | None = None
    voice: str | None = None


def parse_voice_selector(selector: str | None) -> VoiceSelector:
    """Parse a Readio shorthand or delegate a full selector to VoiceRender."""

    if selector is None:
        return VoiceSelector()
    value = selector.strip()
    if not value:
        raise ValueError("voice selector must not be empty")
    if ":" not in value:
        return VoiceSelector(voice=value)

    parsed = voicerender.parse_voice_selector(value)
    return VoiceSelector(
        engine=parsed.engine,
        target_id=parsed.target_id,
        voice=parsed.voice,
    )


__all__ = ["VoiceSelector", "parse_voice_selector"]
