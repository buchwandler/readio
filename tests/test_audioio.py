"""Audio I/O facade tests (plan section 22) and the SoundFile boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest

from readio.audioio import (
    format_available,
    open_audio_writer,
    probe_audio,
    read_audio,
    write_audio,
    write_pcm16_wav,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _tone(frames: int = 64) -> np.ndarray:
    return np.linspace(-0.5, 0.5, frames, dtype=np.float32)


def test_probe_and_read_round_trip_mono(tmp_path: Path) -> None:
    path = tmp_path / "mono.wav"
    audio = _tone()
    write_pcm16_wav(path, audio, 22050)

    info = probe_audio(path)
    assert (info.frames, info.sample_rate, info.channels) == (64, 22050, 1)
    assert (info.format, info.subtype) == ("WAV", "PCM_16")

    read_back, sample_rate = read_audio(path)
    assert sample_rate == 22050
    assert read_back.shape == (64,)
    assert np.allclose(read_back, audio, atol=2e-4)


def test_probe_and_read_round_trip_stereo_preserves_channels(tmp_path: Path) -> None:
    path = tmp_path / "stereo.wav"
    audio = np.stack([_tone(), _tone()], axis=1)
    write_pcm16_wav(path, audio, 48000)

    info = probe_audio(path)
    assert (info.frames, info.sample_rate, info.channels) == (64, 48000, 2)

    read_back, sample_rate = read_audio(path)
    assert sample_rate == 48000
    assert read_back.shape == (64, 2)


def test_write_audio_honors_container_format(tmp_path: Path) -> None:
    path = tmp_path / "clip.flac"
    write_audio(path, _tone(), 16000, file_format="FLAC")

    info = probe_audio(path)
    assert info.format == "FLAC"
    assert info.sample_rate == 16000


def test_format_availability_reports_supported_pairs() -> None:
    assert format_available("WAV", "PCM_16") is True
    assert format_available("NO-SUCH-FORMAT", "NO-SUCH-SUBTYPE") is False


def test_probe_and_read_reject_corrupt_files(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.wav"
    path.write_bytes(b"not audio data")

    with pytest.raises((RuntimeError, OSError)):
        probe_audio(path)
    with pytest.raises((RuntimeError, OSError)):
        read_audio(path)


def test_open_audio_writer_streams_pcm16_chunks(tmp_path: Path) -> None:
    path = tmp_path / "stream.wav"
    writer = open_audio_writer(
        path,
        sample_rate=8000,
        channels=1,
        file_format="WAV",
        subtype="PCM_16",
    )
    try:
        writer.write(_tone(32))
        writer.write(_tone(32))
    finally:
        writer.close()

    info = probe_audio(path)
    assert (info.frames, info.sample_rate, info.subtype) == (64, 8000, "PCM_16")


def test_only_audioio_imports_soundfile() -> None:
    importers = []
    for source in (REPO_ROOT / "readio").rglob("*.py"):
        relative = source.relative_to(REPO_ROOT).as_posix()
        if relative == "readio/audioio.py":
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            if any(name.split(".", 1)[0] == "soundfile" for name in names):
                importers.append(relative)
    assert importers == [], f"soundfile must only be imported by readio/audioio.py: {importers}"
