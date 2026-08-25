from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import Any

import numpy as np
import soundfile as sf

from .audio import AudioSink
from .errors import RenderError
from .formats import AUDIO_FORMATS, AudioFormat, ffmpeg_executable


class SoundFileSink:
    """Write rendered chunks directly to a SoundFile-backed audio file."""

    def __init__(self, path: Path, *, file_format: str, subtype: str) -> None:
        self.path = path
        self._file_format = file_format
        self._subtype = subtype
        self._writer: sf.SoundFile | None = None
        self._sample_rate: int | None = None
        self._channels: int | None = None
        self.sample_count = 0
        self._closed = False

    def write(self, audio: np.ndarray, sample_rate: int) -> None:
        if self._closed:
            raise RuntimeError("audio sink is closed")
        channels = _channel_count(audio)
        if self._writer is None:
            self._sample_rate = sample_rate
            self._channels = channels
            self._writer = sf.SoundFile(
                self.path,
                mode="w",
                samplerate=sample_rate,
                channels=channels,
                format=self._file_format,
                subtype=self._subtype,
            )
        elif sample_rate != self._sample_rate or channels != self._channels:
            raise ValueError("all rendered chunks must use the same sample rate and channel count")
        self._writer.write(audio)
        self.sample_count += len(audio)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._writer is not None:
                self._writer.close()

    def __enter__(self) -> SoundFileSink:  # noqa: PYI034
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class WaveSink(SoundFileSink):
    """Write rendered chunks directly to one PCM16 WAV file."""

    def __init__(self, path: Path) -> None:
        super().__init__(path, file_format="WAV", subtype="PCM_16")


def _channel_count(audio: np.ndarray) -> int:
    if audio.ndim == 1:
        channels = 1
    elif audio.ndim == 2:
        channels = int(audio.shape[1])
    else:
        channels = 0
    if channels <= 0:
        raise ValueError("rendered audio must be a one- or two-dimensional array")
    return channels


def build_ffmpeg_command(
    executable: str,
    path: Path,
    sample_rate: int,
    channels: int,
    audio_format: AudioFormat = "m4a",
) -> list[str]:
    if audio_format != "m4a":
        raise ValueError(f"FFmpeg output format is not supported: {audio_format}")
    return [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-f",
        "f32le",
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-i",
        "pipe:0",
        "-vn",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-f",
        "ipod",
        str(path),
    ]


class FFmpegM4ASink:
    """Stream generated float32 PCM into an FFmpeg M4A encoder."""

    def __init__(self, path: Path, *, executable: str | None = None) -> None:
        self.path = path
        self._executable = executable or ffmpeg_executable()
        self._process: Any = None
        self._stderr_file: Any = None
        self._sample_rate: int | None = None
        self._channels: int | None = None
        self.sample_count = 0
        self._closed = False

    def write(self, audio: np.ndarray, sample_rate: int) -> None:
        if self._closed:
            raise RuntimeError("audio sink is closed")
        channels = _channel_count(audio)
        if self._process is None:
            if self._executable is None:
                raise RenderError(
                    "M4A output requires FFmpeg; install ffmpeg and ensure it is on PATH"
                )
            self._sample_rate = sample_rate
            self._channels = channels
            self._stderr_file = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115
            command = build_ffmpeg_command(
                self._executable,
                self.path,
                sample_rate,
                channels,
            )
            try:
                self._process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stderr=self._stderr_file,
                )
            except OSError as exc:
                self._stderr_file.close()
                self._stderr_file = None
                raise RenderError(f"failed to start FFmpeg for M4A: {exc}") from exc
        elif sample_rate != self._sample_rate or channels != self._channels:
            raise ValueError("all rendered chunks must use the same sample rate and channel count")

        try:
            assert self._process.stdin is not None
            self._process.stdin.write(np.asarray(audio, dtype="<f4", order="C").tobytes())
        except (BrokenPipeError, OSError) as exc:
            raise RenderError(f"FFmpeg failed while encoding M4A: {exc}") from exc
        self.sample_count += len(audio)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._process is None:
            self._close_stderr()
            return
        try:
            if self._process.stdin is not None:
                self._process.stdin.close()
            returncode = self._process.wait()
            if returncode:
                detail = self._stderr_detail()
                message = detail or f"process exited with status {returncode}"
                raise RenderError(f"FFmpeg failed to encode M4A: {message}")
        finally:
            self._close_stderr()

    def _stderr_detail(self) -> str:
        if self._stderr_file is None:
            return ""
        self._stderr_file.seek(0)
        lines = self._stderr_file.read().decode("utf-8", errors="replace").splitlines()
        return lines[-1].strip() if lines else ""

    def _close_stderr(self) -> None:
        if self._stderr_file is not None:
            self._stderr_file.close()
            self._stderr_file = None

    def _abort(self) -> None:
        if self._process is None:
            self._close_stderr()
            return
        try:
            if self._process.stdin is not None:
                self._process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            self._process.terminate()
            self._process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            try:
                self._process.kill()
                self._process.wait()
            except OSError:
                pass
        finally:
            self._close_stderr()

    def __enter__(self) -> FFmpegM4ASink:  # noqa: PYI034
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self._abort()
        else:
            self.close()


def create_audio_sink(path: Path, audio_format: AudioFormat) -> AudioSink:
    spec = AUDIO_FORMATS[audio_format]
    if spec.backend == "soundfile":
        assert spec.soundfile_format is not None
        assert spec.soundfile_subtype is not None
        return SoundFileSink(
            path,
            file_format=spec.soundfile_format,
            subtype=spec.soundfile_subtype,
        )
    if spec.backend == "ffmpeg":
        return FFmpegM4ASink(path)
    raise AssertionError(f"unknown audio backend: {spec.backend}")


@contextmanager
def atomic_audio_path(path: Path, *, force: bool = False) -> Iterator[Path]:
    """Yield a temporary audio path and replace the destination after success."""

    if path.exists() and not force:
        raise FileExistsError(f"output already exists: {path}; use --force to replace it")
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}.",
        suffix=path.suffix or ".wav",
        dir=path.parent,
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        yield temporary
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def atomic_wav_path(path: Path, *, force: bool = False) -> Iterator[Path]:
    """Compatibility wrapper for the generic atomic audio path helper."""

    with atomic_audio_path(path, force=force) as temporary:
        yield temporary
