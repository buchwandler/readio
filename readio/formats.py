from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import soundfile as sf

from .errors import RenderError

AudioFormat = Literal["wav", "mp3", "m4a", "ogg"]
SUPPORTED_AUDIO_FORMATS = ("wav", "mp3", "m4a", "ogg")
_SUPPORTED_SUFFIXES = ", ".join(f".{name}" for name in SUPPORTED_AUDIO_FORMATS)


@dataclass(frozen=True, slots=True)
class AudioFormatSpec:
    name: AudioFormat
    suffix: str
    backend: Literal["soundfile", "ffmpeg"]
    soundfile_format: str | None = None
    soundfile_subtype: str | None = None


AUDIO_FORMATS: dict[AudioFormat, AudioFormatSpec] = {
    "wav": AudioFormatSpec(
        name="wav",
        suffix=".wav",
        backend="soundfile",
        soundfile_format="WAV",
        soundfile_subtype="PCM_16",
    ),
    "mp3": AudioFormatSpec(
        name="mp3",
        suffix=".mp3",
        backend="soundfile",
        soundfile_format="MP3",
        soundfile_subtype="MPEG_LAYER_III",
    ),
    "m4a": AudioFormatSpec(name="m4a", suffix=".m4a", backend="ffmpeg"),
    "ogg": AudioFormatSpec(
        name="ogg",
        suffix=".ogg",
        backend="soundfile",
        soundfile_format="OGG",
        soundfile_subtype="VORBIS",
    ),
}


def audio_format_from_suffix(path: Path) -> AudioFormat | None:
    suffix = path.suffix.lower()
    for audio_format, spec in AUDIO_FORMATS.items():
        if suffix == spec.suffix:
            return audio_format
    return None


def format_suffix(audio_format: AudioFormat) -> str:
    try:
        return AUDIO_FORMATS[audio_format].suffix
    except KeyError as exc:
        raise ValueError(f"unsupported audio format {audio_format!r}") from exc


def resolve_audio_format(
    *,
    requested: str | None,
    output: Path | None,
    default: AudioFormat = "wav",
) -> AudioFormat:
    if requested is not None and requested not in AUDIO_FORMATS:
        raise ValueError(
            f"unsupported audio format {requested!r}; supported formats: "
            f"{', '.join(SUPPORTED_AUDIO_FORMATS)}"
        )

    inferred: AudioFormat | None = None
    if output is not None and output.suffix:
        inferred = audio_format_from_suffix(output)
        if inferred is None:
            raise ValueError(
                f"unsupported audio output extension {output.suffix!r}; supported extensions: "
                f"{_SUPPORTED_SUFFIXES}"
            )

    if requested is not None and inferred is not None and requested != inferred:
        raise ValueError(
            f"--format {requested} conflicts with output extension {output.suffix.lower()}"
        )
    return requested or inferred or default


def normalize_audio_output_path(path: Path, audio_format: AudioFormat) -> Path:
    suffix = format_suffix(audio_format)
    if not path.suffix:
        return path.with_name(path.name + suffix)
    inferred = audio_format_from_suffix(path)
    if inferred != audio_format:
        raise ValueError(
            f"--format {audio_format} conflicts with output extension {path.suffix.lower()}"
        )
    return path


def ffmpeg_executable() -> str | None:
    return shutil.which("ffmpeg")


def soundfile_format_available(audio_format: AudioFormat) -> bool:
    spec = AUDIO_FORMATS[audio_format]
    if spec.backend != "soundfile":
        return False
    if spec.soundfile_format is None or spec.soundfile_subtype is None:
        return False
    try:
        return bool(sf.check_format(spec.soundfile_format, spec.soundfile_subtype))
    except (RuntimeError, ValueError):
        return False


def audio_format_available(audio_format: AudioFormat) -> bool:
    spec = AUDIO_FORMATS[audio_format]
    if spec.backend == "ffmpeg":
        return ffmpeg_executable() is not None
    return soundfile_format_available(audio_format)


def ensure_audio_format_available(audio_format: AudioFormat) -> None:
    spec = AUDIO_FORMATS[audio_format]
    if spec.backend == "ffmpeg":
        if ffmpeg_executable() is None:
            raise RenderError(
                "M4A output requires FFmpeg; install ffmpeg and ensure it is on PATH"
            )
        return
    if not soundfile_format_available(audio_format):
        raise RenderError(
            f"{audio_format.upper()} output is not supported by the installed libsndfile build"
        )


def audio_format_diagnostics() -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    executable = ffmpeg_executable()
    for audio_format, spec in AUDIO_FORMATS.items():
        item: dict[str, object] = {
            "available": audio_format_available(audio_format),
            "backend": spec.backend,
        }
        if spec.backend == "ffmpeg":
            item["executable"] = executable or "not found"
        result[audio_format] = item
    return result
