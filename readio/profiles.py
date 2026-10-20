"""RenderProfile lifecycle and role operations delegated to VoiceRender."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voicerender  # type: ignore[import-not-found]

from ._outputs import save_specialist_file


def _persist_profile(
    profile: voicerender.RenderProfile, path: str | Path, *, force: bool = False
) -> Path:
    return save_specialist_file(
        path,
        lambda destination: voicerender.save_render_profile(profile, destination),
        force=force,
    )


def _binding_source(
    *,
    voicefolio_ref: str | None = None,
    reference_wav: str | Path | None = None,
    prepared_voice: str | Path | None = None,
    voice_source: str | Path | dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    choices = (voicefolio_ref, reference_wav, prepared_voice, voice_source)
    if sum(item is not None for item in choices) > 1:
        raise ValueError("voice source options are mutually exclusive")
    if voicefolio_ref is not None:
        return voicerender.managed_reference_source(voicefolio_ref)
    if reference_wav is not None:
        return voicerender.local_reference_source(reference_wav)
    if prepared_voice is not None:
        path = Path(prepared_voice).expanduser()
        return voicerender.local_prepared_voice_source(path, format=path.suffix.lstrip("."))
    if isinstance(voice_source, (str, Path)):
        return dict(voicerender.load_voice_source(voice_source))
    return voice_source


def create_profile(
    *,
    engine: str,
    target: str,
    language: str,
    role: str = "narrator",
    voice: str | None = None,
    voicefolio_ref: str | None = None,
    reference_wav: str | Path | None = None,
    prepared_voice: str | Path | None = None,
    voice_source: str | Path | dict[str, Any] | None = None,
    speaker: str | int | None = None,
    options: dict[str, Any] | None = None,
    offline: bool = False,
    refresh: bool = False,
    output: str | Path,
    force: bool = False,
) -> object:
    source = _binding_source(
        voicefolio_ref=voicefolio_ref,
        reference_wav=reference_wav,
        prepared_voice=prepared_voice,
        voice_source=voice_source,
    )
    profile = voicerender.create_render_profile(
        engine=engine,
        target_id=target,
        language=language,
        role=role,
        voice=voice,
        speaker=speaker,
        voice_source=source,
        options=dict(options or {}),
        offline=offline,
        refresh=refresh,
    )
    _persist_profile(profile, output, force=force)
    return profile


def load_profile(profile: str | Path) -> object:
    return voicerender.load_render_profile(profile)


def validate_profile(profile: str | Path) -> object:
    return voicerender.validate_render_profile(profile)


def refresh_profile(
    profile: str | Path,
    *,
    output: str | Path | None = None,
    force: bool = False,
) -> object:
    refreshed = voicerender.refresh_render_profile(profile)
    if output is not None:
        _persist_profile(refreshed, output, force=force)
    return refreshed


def list_profile_roles(profile: str | Path) -> object:
    return voicerender.list_profile_roles(profile)


def set_profile_role(
    profile: str | Path,
    role: str,
    *,
    engine: str,
    target: str,
    language: str,
    voice: str | None = None,
    voicefolio_ref: str | None = None,
    reference_wav: str | Path | None = None,
    prepared_voice: str | Path | None = None,
    voice_source: str | Path | dict[str, Any] | None = None,
    speaker: str | int | None = None,
    options: dict[str, Any] | None = None,
    offline: bool = False,
    refresh: bool = False,
    make_default: bool = False,
    output: str | Path | None = None,
    in_place: bool = False,
) -> object:
    if (output is None) == (not in_place):
        raise ValueError("exactly one of output or in_place is required")
    source = _binding_source(
        voicefolio_ref=voicefolio_ref,
        reference_wav=reference_wav,
        prepared_voice=prepared_voice,
        voice_source=voice_source,
    )
    updated = voicerender.set_profile_role(
        profile,
        role,
        engine=engine,
        target_id=target,
        language=language,
        voice=voice,
        speaker=speaker,
        voice_source=source,
        options=dict(options or {}),
        offline=offline,
        refresh=refresh,
        make_default=make_default,
    )
    destination = Path(profile).expanduser() if in_place else Path(output).expanduser()  # type: ignore[arg-type]
    if in_place:
        voicerender.save_render_profile(updated, destination)
    else:
        _persist_profile(updated, destination)
    return updated


def remove_profile_role(
    profile: str | Path,
    role: str,
    *,
    output: str | Path | None = None,
    in_place: bool = False,
    new_default_role: str | None = None,
) -> object:
    if (output is None) == (not in_place):
        raise ValueError("exactly one of output or in_place is required")
    updated = voicerender.remove_profile_role(
        profile,
        role,
        new_default_role=new_default_role,
    )
    destination = Path(profile).expanduser() if in_place else Path(output).expanduser()  # type: ignore[arg-type]
    if in_place:
        voicerender.save_render_profile(updated, destination)
    else:
        _persist_profile(updated, destination)
    return updated


def set_default_profile_role(
    profile: str | Path,
    role: str,
    *,
    output: str | Path | None = None,
    in_place: bool = False,
) -> object:
    if (output is None) == (not in_place):
        raise ValueError("exactly one of output or in_place is required")
    updated = voicerender.set_default_profile_role(profile, role)
    destination = Path(profile).expanduser() if in_place else Path(output).expanduser()  # type: ignore[arg-type]
    if in_place:
        voicerender.save_render_profile(updated, destination)
    else:
        _persist_profile(updated, destination)
    return updated


__all__ = [
    "create_profile",
    "list_profile_roles",
    "load_profile",
    "refresh_profile",
    "remove_profile_role",
    "set_default_profile_role",
    "set_profile_role",
    "validate_profile",
]
