from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType

import numpy as np
import soundfile as sf


class WaveSink:
    """Write rendered chunks directly to one PCM16 WAV file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._writer: sf.SoundFile | None = None
        self._sample_rate: int | None = None
        self._channels: int | None = None
        self.sample_count = 0
        self._closed = False

    def write(self, audio: np.ndarray, sample_rate: int) -> None:
        if self._closed:
            raise RuntimeError("audio sink is closed")
        if audio.ndim == 1:
            channels = 1
        elif audio.ndim == 2:
            channels = int(audio.shape[1])
        else:
            channels = 0
        if channels <= 0:
            raise ValueError("rendered audio must be a one- or two-dimensional array")
        if self._writer is None:
            self._sample_rate = sample_rate
            self._channels = channels
            self._writer = sf.SoundFile(
                self.path,
                mode="w",
                samplerate=sample_rate,
                channels=channels,
                format="WAV",
                subtype="PCM_16",
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

    def __enter__(self) -> WaveSink:  # noqa: PYI034
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


@contextmanager
def atomic_wav_path(path: Path, *, force: bool = False) -> Iterator[Path]:
    """Yield a temporary WAV path and replace the destination after success."""

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
