"""Readio-owned audio I/O boundary.

SoundFile is an implementation detail behind this module. Everything else in
Readio probes, reads, and writes audio through these operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


@dataclass(frozen=True, slots=True)
class AudioFileInfo:
    """Probe metadata for one audio file."""

    frames: int
    sample_rate: int
    channels: int
    format: str
    subtype: str


def probe_audio(path: Path) -> AudioFileInfo:
    """Return format metadata for one audio file without decoding samples."""
    info = sf.info(path)
    return AudioFileInfo(
        frames=int(info.frames),
        sample_rate=int(info.samplerate),
        channels=int(info.channels),
        format=str(info.format),
        subtype=str(info.subtype),
    )


def read_audio(path: Path) -> tuple[np.ndarray, int]:
    """Read one audio file as float32 samples plus its sample rate."""
    audio, sample_rate = sf.read(path, always_2d=False, dtype="float32")
    return audio, int(sample_rate)


def write_audio(
    path: Path,
    audio: np.ndarray,
    sample_rate: int,
    *,
    file_format: str | None = None,
    subtype: str = "PCM_16",
) -> None:
    """Write samples to one audio file in the requested container format."""
    sf.write(path, audio, sample_rate, subtype=subtype, format=file_format)


def write_pcm16_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    """Write samples to one PCM16 WAV file."""
    sf.write(path, audio, sample_rate, subtype="PCM_16", format="WAV")


def format_available(file_format: str, subtype: str) -> bool:
    """Return whether the installed audio backend supports one format pair."""
    return bool(sf.check_format(file_format, subtype))


def open_audio_writer(
    path: Path,
    *,
    sample_rate: int,
    channels: int,
    file_format: str,
    subtype: str,
) -> Any:
    """Open one streaming audio writer for incremental chunk output."""
    return sf.SoundFile(
        path,
        mode="w",
        samplerate=sample_rate,
        channels=channels,
        format=file_format,
        subtype=subtype,
    )


__all__ = [
    "AudioFileInfo",
    "format_available",
    "open_audio_writer",
    "probe_audio",
    "read_audio",
    "write_audio",
    "write_pcm16_wav",
]
