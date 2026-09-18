"""Audio output and rendering for Readio.

This module provides audio output sinks and the RenderedUnit type
for normalized live/streaming results.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(slots=True)
class RenderedUnit:
    """Normalized view of a rendered unit for live/streaming mode.

    This provides a neutral representation that adapters convert
    their native unit results into.
    """

    index: int
    plan_unit_id: str
    content_hash: str | None
    audio: np.ndarray
    sample_rate: int
    markers: tuple[dict[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def release_audio(self) -> None:
        """Release the audio buffer."""
        self.audio = np.array([], dtype=np.float32)


@dataclass(frozen=True, slots=True)
class AudioSink:
    """Base class for audio output sinks."""

    sample_rate: int
    channels: int = 1

    def write(self, audio: np.ndarray) -> None:
        """Write audio data to the sink."""
        raise NotImplementedError

    def close(self) -> None:
        """Close the sink."""
        pass


@dataclass(frozen=True, slots=True)
class FileSink(AudioSink):
    """Audio sink that writes to a file."""

    path: str
    format: str = "wav"

    def write(self, audio: np.ndarray) -> None:
        """Write audio data to a file."""
        import soundfile as sf

        sf.write(self.path, audio, self.sample_rate, format=self.format)


@dataclass(frozen=True, slots=True)
class PlaybackSink(AudioSink):
    """Audio sink for live playback."""

    device: int | str | None = None
    queue_size: int = 2

    def write(self, audio: np.ndarray) -> None:
        """Write audio data for playback."""
        # This is a placeholder; actual playback implementation
        # depends on the platform
        raise NotImplementedError("PlaybackSink.write() not implemented")


def render_to_audio_job(
    units: list[RenderedUnit],
    sample_rate: int = 24000,
) -> Any:
    """Convert rendered units to an AudioJob.

    This is the bounded render path that creates an AudioJob
    from the rendered units.
    """
    from audiocompose import AudioBufferSource, AudioClip, AudioJob, OutputPolicy, Silence

    items = []
    for unit in units:
        if unit.audio.size > 0:
            items.append(
                AudioClip(
                    id=f"unit:{unit.index}",
                    source=AudioBufferSource(unit.audio, unit.sample_rate),
                    metadata={
                        "plan_unit_id": unit.plan_unit_id,
                        "content_hash": unit.content_hash,
                        **dict(unit.metadata),
                    },
                )
            )

    return AudioJob(
        items=tuple(items),
        output=OutputPolicy(sample_rate=sample_rate),
    )


__all__ = [
    "AudioSink",
    "FileSink",
    "PlaybackSink",
    "RenderedUnit",
    "render_to_audio_job",
]
