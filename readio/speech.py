"""Direct text-to-speech convenience over VoiceRender."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import voicerender  # type: ignore[import-not-found]

from .playback import play_audio


@dataclass(frozen=True, slots=True)
class VoiceSelector:
    engine: str | None = None
    target_id: str | None = None
    voice: str | None = None


def parse_voice_selector(selector: str | None) -> VoiceSelector:
    """Parse ``engine:target/voice`` into neutral VoiceRender request fields."""
    if selector is None:
        return VoiceSelector()
    value = selector.strip()
    if not value:
        raise ValueError("voice selector must not be empty")
    if ":" not in value:
        return VoiceSelector(voice=value)

    engine, selection = value.split(":", 1)
    if not engine or not selection:
        raise ValueError("voice selector must use engine:target[/voice]")
    if "/" in selection:
        target_id, voice = selection.split("/", 1)
        if not target_id or not voice:
            raise ValueError("voice selector must use engine:target/voice")
        return VoiceSelector(engine=engine, target_id=target_id, voice=voice)
    return VoiceSelector(engine=engine, target_id=selection)


def speak(
    text: str,
    *,
    voice: str | None = None,
    engine: str | None = None,
    target: str | None = None,
    language: str = "en-US",
    speaker: str | int | None = None,
    offline: bool = False,
    refresh: bool = False,
    engine_options: Mapping[str, Any] | None = None,
) -> object:
    """Resolve, synthesize, and play literal text through VoiceRender."""
    selector = parse_voice_selector(voice)
    selection, _ = voicerender.resolve_voice(
        voicerender.EngineRequest(
            engine=engine or selector.engine,
            target_id=target or selector.target_id,
            language=language,
            voice=selector.voice,
            speaker=speaker,
            offline=offline,
            refresh=refresh,
            engine_options=dict(engine_options or {}),
        )
    )
    request = voicerender.SpeechRequest(
        id="readio-speak",
        text=text,
        language=language,
        voice=selection.voice,
        speaker=selection.speaker,
    )
    with voicerender.open_session(selection) as session:
        rendered = voicerender.synthesize(session, request)
    play_audio(rendered.audio, rendered.sample_rate)
    return rendered
