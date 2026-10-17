"""Voice discovery and voice-artifact workflows delegated to VoiceRender."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import voicerender  # type: ignore[import-not-found]
from voicerender import VoiceCloneRequest  # type: ignore[import-not-found]
from voicerender.voice_design import (
    Qwen3VoiceDesignRecipe,
    design_qwen3_reference,
)

from ._outputs import save_specialist_file


def list_voices(
    *,
    engine: str | None = None,
    target: str | None = None,
    language: str | None = None,
    kind: str | None = None,
    offline: bool = False,
    refresh: bool = False,
) -> tuple[object, ...]:
    voices = voicerender.list_voices(
        engine=engine,
        target_id=target,
        language=language,
        offline=offline,
        refresh=refresh,
    )
    if kind is None:
        return voices
    selected = kind.casefold()
    return tuple(
        voice
        for voice in voices
        if str(getattr(voice, "kind", "")).casefold().rsplit(".", 1)[-1] == selected
    )


def inspect_voice(selector: str, *, language: str | None = None) -> object:
    return voicerender.inspect_voice(selector, language=language)


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def create_voice_source(
    *,
    reference_wav: str | Path | None = None,
    voicefolio_ref: str | None = None,
    transcript: str | None = None,
    language: str | None = None,
    name: str | None = None,
    metadata: dict[str, Any] | None = None,
    output: str | Path,
    force: bool = False,
) -> object:
    source = voicerender.create_voice_source(
        reference_wav=reference_wav,
        voicefolio_ref=voicefolio_ref,
        transcript=transcript,
        language=language,
        name=name,
        metadata=metadata,
    )
    serialized = _json_value(source)
    if not isinstance(serialized, dict):
        raise TypeError("VoiceRender returned an unsupported voice-source value")
    save_specialist_file(
        output,
        lambda destination: voicerender.save_voice_source(serialized, destination),
        force=force,
    )
    return source


def show_voice_source(path: str | Path) -> object:
    return voicerender.load_voice_source(path)


def verify_voice_source(
    path: str | Path,
    *,
    offline: bool = False,
) -> tuple[object, ...]:
    source = voicerender.load_voice_source(path)
    return voicerender.validate_voice_source(source, offline=offline)


def clone_voice(
    *,
    engine: str,
    target: str,
    language: str,
    role: str = "narrator",
    reference_wav: str | Path | None = None,
    voice_source: str | Path | dict[str, Any] | None = None,
    voicefolio_ref: str | None = None,
    transcript: str | None = None,
    speaker: str | int | None = None,
    options: dict[str, Any] | None = None,
    offline: bool = False,
    refresh: bool = False,
    output: str | Path,
    force: bool = False,
) -> object:
    sources = (reference_wav, voice_source, voicefolio_ref)
    if sum(source is not None for source in sources) != 1:
        raise ValueError(
            "exactly one of reference_wav, voice_source, or voicefolio_ref is required"
        )
    request = VoiceCloneRequest(
        engine=engine,
        target_id=target,
        language=language,
        reference_wav=Path(reference_wav) if reference_wav is not None else None,
        voice_source=voice_source,
        voicefolio_ref=voicefolio_ref,
        transcript=transcript,
        speaker=speaker,
        options=dict(options or {}),
        offline=offline,
        refresh=refresh,
    )
    profile = voicerender.clone_voice_profile(request, role=role)
    save_specialist_file(
        output,
        lambda destination: voicerender.save_render_profile(profile, destination),
        force=force,
    )
    return profile


def design_voice(
    *,
    prompt: str,
    reference_text: str,
    output: str | Path,
    language: str = "en",
    target: str = "voice-design-1.7b",
    quality: str = "int4",
    provider: str = "cpu",
    seed: int | None = None,
    temperature: float = 0.9,
    top_k: int = 50,
    repetition_penalty: float = 1.05,
    max_new_tokens: int = 128,
    cache_dir: str | Path | None = None,
    catalog: str | Path | None = None,
    source_output: str | Path | None = None,
    force: bool = False,
) -> object:
    destination = Path(output).expanduser()
    if destination.exists() or destination.is_symlink():
        if not force:
            raise FileExistsError(f"output already exists: {destination}")
        if destination.is_symlink() or not destination.is_file():
            raise ValueError(f"refusing to replace non-file output: {destination}")
    recipe = Qwen3VoiceDesignRecipe(
        prompt=prompt,
        reference_text=reference_text,
        language=language,
        target_id=target,
        quality=quality,
        provider=provider,
        seed=seed,
        temperature=temperature,
        top_k=top_k,
        repetition_penalty=repetition_penalty,
        max_new_tokens=max_new_tokens,
    )
    return design_qwen3_reference(
        recipe,
        destination,
        cache_dir=cache_dir,
        catalog=catalog,
        source_output=source_output,
        force=force,
    )


__all__ = [
    "Qwen3VoiceDesignRecipe",
    "clone_voice",
    "create_voice_source",
    "design_voice",
    "inspect_voice",
    "list_voices",
    "show_voice_source",
    "verify_voice_source",
]
