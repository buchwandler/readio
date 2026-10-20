"""Direct text-to-speech convenience over VoiceRender."""

from __future__ import annotations

import wave
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import voicerender  # type: ignore[import-not-found]

from .playback import play_audio


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


def _profile_request(
    profile_path: str | Path,
    role: str | None,
    *,
    language: str | None,
    speaker: str | int | None,
    offline: bool,
    refresh: bool,
    engine_options: Mapping[str, Any] | None,
) -> tuple[Any, str]:
    profile = voicerender.load_render_profile(profile_path)
    role_name = role or profile.default_role
    try:
        role_profile = profile.roles[role_name]
    except KeyError as error:
        raise ValueError(f"profile has no role {role_name!r}") from error
    binding = role_profile.binding
    if not isinstance(binding, Mapping):
        raise TypeError(f"profile role {role_name!r} has an invalid binding")
    base_options = binding.get("options", {})
    if not isinstance(base_options, Mapping):
        raise TypeError(f"profile role {role_name!r} has invalid engine options")
    options = dict(base_options)
    voice_source = binding.get("voice_source")
    if voice_source is not None:
        options["voice_source"] = voice_source
    selected_options = dict(binding.get("engine_options", base_options))
    selected_options.update(engine_options or {})
    selection, _diagnostics = voicerender.resolve_voice(
        voicerender.EngineRequest(
            engine=binding.get("engine"),
            target_id=binding.get("target_id"),
            language=language or role_profile.language,
            voice=binding.get("voice"),
            speaker=speaker if speaker is not None else binding.get("speaker"),
            options=options,
            offline=offline,
            refresh=refresh,
            engine_options=selected_options,
        )
    )
    return selection, role_name


def _write_speech_wav(rendered: Any, output: str | Path) -> Path:
    path = Path(output).expanduser()
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"output already exists: {path}")
    audio = np.asarray(rendered.audio, dtype=np.float32)
    if audio.ndim == 1:
        channels = 1
    elif audio.ndim == 2 and audio.shape[1] > 0:
        channels = int(audio.shape[1])
    else:
        raise ValueError("VoiceRender returned audio with an unsupported shape")
    sample_rate = int(rendered.sample_rate)
    if sample_rate <= 0:
        raise ValueError("VoiceRender returned an invalid sample rate")
    pcm = np.rint(np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = path.open("xb")
    except FileExistsError:
        raise FileExistsError(f"output already exists: {path}")
    try:
        with raw, wave.open(raw, "wb") as stream:
            stream.setnchannels(channels)
            stream.setsampwidth(2)
            stream.setframerate(sample_rate)
            stream.writeframes(pcm.tobytes())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def speak(
    text: str,
    *,
    voice: str | None = None,
    engine: str | None = None,
    target: str | None = None,
    language: str | None = None,
    speaker: str | int | None = None,
    offline: bool = False,
    refresh: bool = False,
    engine_options: Mapping[str, Any] | None = None,
    profile: str | Path | None = None,
    role: str | None = None,
    output: str | Path | None = None,
    no_play: bool = False,
) -> Any:
    """Resolve and synthesize literal text through VoiceRender, optionally playing or saving WAV."""
    if profile is not None and any(value is not None for value in (voice, engine, target)):
        raise ValueError("--profile is mutually exclusive with --voice, --engine, and --target")
    if role is not None and profile is None:
        raise ValueError("--role requires --profile")
    if profile is not None:
        selection, _role_name = _profile_request(
            profile,
            role,
            language=language,
            speaker=speaker,
            offline=offline,
            refresh=refresh,
            engine_options=engine_options,
        )
    else:
        selector = parse_voice_selector(voice)
        selection, _ = voicerender.resolve_voice(
            voicerender.EngineRequest(
                engine=engine or selector.engine,
                target_id=target or selector.target_id,
                language=language or "en-US",
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
        language=language or selection.language,
        voice=selection.voice,
        speaker=selection.speaker,
    )
    with voicerender.open_session(selection) as session:
        rendered = voicerender.synthesize(session, request)
    if output is not None:
        _write_speech_wav(rendered, output)
    if not no_play:
        play_audio(rendered.audio, rendered.sample_rate)
    return rendered
