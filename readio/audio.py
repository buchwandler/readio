"""Audio output and rendering for Readio.

This module provides the audio sink protocol, render progress/summary
types, and the Readio-owned PlaybackSink.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Protocol

import numpy as np
from typing_extensions import Self

# ---------------------------------------------------------------------------
# AudioSink protocol
# ---------------------------------------------------------------------------


class AudioSink(Protocol):
    """Structural protocol for audio output sinks."""

    def write(self, audio: np.ndarray, sample_rate: int) -> None: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# RenderSummary / RenderProgress
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RenderSummary:
    """Aggregate audio summary for one execution."""

    sample_rate: int = 0
    sample_count: int = 0
    channels: int = 0
    document_metadata: Mapping[str, Any] = field(default_factory=dict)
    markers: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class RenderProgress:
    """Progress event emitted during streaming render."""

    completed_units: int
    total_units: int | None
    sample_count: int
    sample_rate: int


RenderProgressCallback = Callable[[RenderProgress], None]


# ---------------------------------------------------------------------------
# Channel-count helper
# ---------------------------------------------------------------------------


def _channel_count(audio: np.ndarray) -> int:
    """Return the number of channels for a one- or two-dimensional audio array."""
    if audio.ndim == 1:
        return 1
    if audio.ndim == 2:
        return int(audio.shape[1])
    raise ValueError("audio chunks must be one- or two-dimensional")


# ---------------------------------------------------------------------------
# PlaybackSink
# ---------------------------------------------------------------------------


class PlaybackSink:
    """Audio sink for interactive playback through Readio's backend."""

    def __init__(self, cfg: Any) -> None:
        self._cfg = cfg
        self._player: Any = None
        self._sample_rate: int | None = None
        self._channels: int | None = None
        self._closed = False

    def write(self, audio: np.ndarray, sample_rate: int) -> None:
        if self._closed:
            raise RuntimeError("audio sink is closed")
        channels = _channel_count(audio)
        if self._player is None:
            from .playback import SoundDevicePlayback

            self._sample_rate = sample_rate
            self._channels = channels
            self._player = SoundDevicePlayback(
                device=self._cfg.device,
                queue_size=self._cfg.queue_size,
            ).start(sample_rate, channels)
        elif sample_rate != self._sample_rate or channels != self._channels:
            raise ValueError("all rendered chunks must use the same sample rate and channel count")
        self._player.submit(np.asarray(audio, dtype=np.float32))

    def finish(self) -> None:
        """Drain all queued audio before returning."""
        if self._player is not None:
            self._player.drain()

    def close(self) -> None:
        """Close the playback backend. Idempotent."""
        if self._closed:
            return
        self._closed = True
        if self._player is not None:
            self._player.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


__all__ = [
    "AudioSink",
    "PlaybackSink",
    "RenderProgress",
    "RenderProgressCallback",
    "RenderSummary",
]
