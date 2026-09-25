"""Audio output and rendering for Readio.

This module provides the audio sink protocol, render progress/summary
types, the PlaybackSink, and the streaming render_prepared() entry point.
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
    """Aggregated result of a streaming render."""

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
    """Audio sink for live playback via PyKokoro.

    The player is created lazily on the first write so that sample rate
    and channel count are discovered from the audio data itself.
    """

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
            from pykokoro.playback import SoundDevicePlayer

            self._sample_rate = sample_rate
            self._channels = channels
            self._player = SoundDevicePlayer(
                sample_rate,
                device=self._cfg.device,
                queue_size=self._cfg.queue_size,
                channels=channels,
            ).start()
        elif sample_rate != self._sample_rate or channels != self._channels:
            raise ValueError("all rendered chunks must use the same sample rate and channel count")

        self._player.submit(audio)

    def finish(self) -> None:
        """Drain the playback player."""
        if self._player is not None:
            self._player.drain()

    def close(self) -> None:
        """Close the playback player.  Idempotent."""
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


# ---------------------------------------------------------------------------
# render_prepared()
# ---------------------------------------------------------------------------


def render_prepared(
    prepared: Any,
    sink: AudioSink,
    *,
    indices: tuple[int, ...] | None = None,
    on_progress: RenderProgressCallback | None = None,
) -> RenderSummary:
    """Render prepared units one at a time, streaming to *sink*.

    Each result is released in a ``finally`` block so that audio memory
    is freed even when the sink raises.
    """
    total_units = len(indices) if indices is not None else len(prepared.units)

    sample_rate = 0
    sample_count = 0
    channels = 0
    completed_units = 0
    markers: list[dict[str, Any]] = []

    if on_progress is not None:
        on_progress(RenderProgress(0, total_units, 0, 0))

    for result in prepared.render(indices=indices):
        try:
            audio = result.audio
            chunk_rate = int(result.sample_rate)
            chunk_channels = _channel_count(audio)

            if sample_count and (chunk_rate != sample_rate or chunk_channels != channels):
                raise ValueError(
                    "all rendered chunks must use the same sample rate and channel count"
                )

            # Write before declaring the unit complete.
            sink.write(audio, chunk_rate)

            for marker in result.markers:
                markers.append(
                    {
                        **marker,
                        "sample_offset": int(marker["sample_offset"]) + sample_count,
                    }
                )

            sample_rate = chunk_rate
            channels = chunk_channels
            sample_count += int(audio.shape[0])
            completed_units += 1

            if on_progress is not None:
                on_progress(
                    RenderProgress(
                        completed_units,
                        total_units,
                        sample_count,
                        sample_rate,
                    )
                )
        finally:
            result.release_audio()

    return RenderSummary(
        sample_rate=sample_rate,
        sample_count=sample_count,
        channels=channels,
        document_metadata=dict(getattr(prepared, "document_metadata", {})),
        markers=tuple(markers),
    )


# ---------------------------------------------------------------------------
__all__ = [
    "AudioSink",
    "PlaybackSink",
    "RenderProgress",
    "RenderProgressCallback",
    "RenderSummary",
    "render_prepared",
]
