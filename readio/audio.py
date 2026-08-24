from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Protocol

import numpy as np

from .config import ReaderSettings


class AudioSink(Protocol):
    """Synchronous destination for one rendered waveform chunk."""

    def write(self, audio: np.ndarray, sample_rate: int) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RenderSummary:
    sample_rate: int = 0
    sample_count: int = 0
    channels: int = 0
    document_metadata: Mapping[str, Any] = field(default_factory=dict)
    markers: tuple[dict[str, Any], ...] = ()


class PlaybackSink:
    """Send rendered chunks through one persistent PyKokoro player."""

    def __init__(self, cfg: ReaderSettings) -> None:
        self._cfg = cfg
        self._player: Any = None
        self._sample_rate: int | None = None
        self._channels: int | None = None
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
        if self._player is None:
            from pykokoro.playback import SoundDevicePlayer

            self._sample_rate = sample_rate
            self._channels = channels
            self._player = SoundDevicePlayer(
                sample_rate,
                device=self._cfg.device,
                queue_size=self._cfg.queue_size,
                channels=channels,
            )
            self._player.start()
        elif sample_rate != self._sample_rate or channels != self._channels:
            raise ValueError("all rendered chunks must use the same sample rate and channel count")
        self._player.submit(audio)

    def finish(self) -> None:
        if self._player is not None:
            self._player.drain()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._player is not None:
                self._player.close()

    def __enter__(self) -> PlaybackSink:  # noqa: PYI034
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def render_prepared(
    prepared: Any,
    sink: AudioSink,
    *,
    indices: tuple[int, ...] | None = None,
) -> RenderSummary:
    sample_rate = 0
    sample_count = 0
    channels = 0
    markers: list[dict[str, Any]] = []
    metadata = dict(getattr(prepared, "document_metadata", {}) or {})

    for result in prepared.render(indices=indices):
        try:
            audio = result.audio
            if audio.ndim == 1:
                result_channels = 1
            elif audio.ndim == 2:
                result_channels = int(audio.shape[1])
            else:
                result_channels = 0
            if result_channels <= 0:
                raise ValueError("rendered audio must be a one- or two-dimensional array")
            if not sample_count:
                sample_rate = int(result.sample_rate)
                channels = result_channels
                if not metadata:
                    metadata = dict(getattr(result, "document_metadata", {}) or {})
            elif result.sample_rate != sample_rate or result_channels != channels:
                raise ValueError(
                    "all rendered chunks must use the same sample rate and channel count"
                )
            sink.write(audio, result.sample_rate)
            markers.extend(
                {
                    **marker,
                    "sample_offset": int(marker["sample_offset"]) + sample_count,
                }
                for marker in getattr(result, "markers", ())
            )
            sample_count += len(audio)
        finally:
            result.release_audio()

    return RenderSummary(
        sample_rate=sample_rate,
        sample_count=sample_count,
        channels=channels,
        document_metadata=metadata,
        markers=tuple(markers),
    )
