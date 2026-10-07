"""Backend-neutral transcript values used by verification APIs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TranscriptWord:
    text: str
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    text: str
    start_seconds: float
    end_seconds: float
    words: tuple[TranscriptWord, ...] = ()


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    segments: tuple[TranscriptSegment, ...] = ()
    words: tuple[TranscriptWord, ...] = ()
    backend: str = "redux"
    model: str = "moondream/parakeet-redux"
    device: str = "cpu"
    load_seconds: float | None = None
    transcription_seconds: float = 0.0
