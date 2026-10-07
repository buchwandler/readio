"""Lazy Moondream Photon / Parakeet Redux integration boundary.

This is the sole Readio module permitted to import the optional ``moondream``
package. Transcript values and errors crossing this module are backend-neutral.
"""

from __future__ import annotations

import importlib.metadata
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from time import perf_counter
from types import TracebackType
from typing import Any, Literal, Self

from ..verification.types import TranscriptionResult, TranscriptSegment, TranscriptWord

DEFAULT_REDUX_MODEL = "moondream/parakeet-redux"
TimestampMode = Literal["none", "segment", "word"]


class ReduxIntegrationError(RuntimeError):
    """A typed failure at the optional Redux integration boundary."""

    code = "verification.backend_error"


class ReduxTimestampError(ReduxIntegrationError):
    """Redux returned malformed timestamp structure or coordinates."""

    code = "verification.invalid_timestamps"


def moondream_version() -> str | None:
    try:
        return importlib.metadata.version("moondream")
    except (importlib.metadata.PackageNotFoundError, OSError, ValueError):
        return None


def _field(value: object, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _finite_time(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReduxTimestampError(f"{label} must be a numeric timestamp")
    result = float(value)
    if not math.isfinite(result):
        raise ReduxTimestampError(f"{label} must be finite")
    if result < 0:
        raise ReduxTimestampError(f"{label} must not be negative")
    return result


def _interval(value: object, *, label: str) -> tuple[float, float]:
    start = _finite_time(_field(value, "start", None), label=f"{label}.start")
    end = _finite_time(_field(value, "end", None), label=f"{label}.end")
    if end < start:
        raise ReduxTimestampError(f"{label}.end must not precede {label}.start")
    return start, end


def _sequence(value: object, *, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ReduxTimestampError(f"{label} must be a sequence")
    return value


def parse_transcription(
    raw: object,
    *,
    model: str = DEFAULT_REDUX_MODEL,
    device: str = "cpu",
    load_seconds: float | None = None,
    transcription_seconds: float = 0.0,
    timestamps: TimestampMode = "word",
) -> TranscriptionResult:
    """Parse current nested Redux records and older aggregate result shapes."""
    text_value = _field(raw, "text", "")
    if text_value is None:
        text_value = ""
    if not isinstance(text_value, str):
        raise ReduxIntegrationError("Redux transcript text must be a string")
    text = text_value
    segments_value = _field(raw, "segments", None)
    segments: list[TranscriptSegment] = []
    words: list[TranscriptWord] = []

    if segments_value is not None:
        for index, segment_raw in enumerate(_sequence(segments_value, label="segments")):
            label = f"segments[{index}]"
            start, end = _interval(segment_raw, label=label)
            segment_text_value = _field(segment_raw, "text", "")
            if segment_text_value is None:
                segment_text_value = ""
            if not isinstance(segment_text_value, str):
                raise ReduxIntegrationError(f"{label}.text must be a string")
            segment_words: list[TranscriptWord] = []
            raw_segment_words = _field(segment_raw, "words", None)
            if raw_segment_words is not None:
                for word_index, word_raw in enumerate(
                    _sequence(raw_segment_words, label=f"{label}.words")
                ):
                    word = _parse_word(word_raw, f"{label}.words[{word_index}]")
                    if word.start_seconds < start or word.end_seconds > end:
                        raise ReduxTimestampError(
                            f"{label}.words[{word_index}] lies outside its segment interval"
                        )
                    if segment_words and word.start_seconds < segment_words[-1].start_seconds:
                        raise ReduxTimestampError(f"{label}.words timestamps move backwards")
                    segment_words.append(word)
                    words.append(word)
            if segments and start < segments[-1].start_seconds:
                raise ReduxTimestampError("segment timestamps move backwards")
            segments.append(
                TranscriptSegment(
                    text=segment_text_value,
                    start_seconds=start,
                    end_seconds=end,
                    words=tuple(segment_words),
                )
            )

    if not words:
        raw_words = _field(raw, "words", None)
        if raw_words is None:
            raw_words = _field(raw, "timestamps", None)
        if raw_words is not None:
            for index, word_raw in enumerate(_sequence(raw_words, label="words")):
                word = _parse_word(word_raw, f"words[{index}]")
                if words and word.start_seconds < words[-1].start_seconds:
                    raise ReduxTimestampError("word timestamps move backwards")
                words.append(word)

    if not text and words:
        text = " ".join(word.text for word in words)
    return TranscriptionResult(
        text=text,
        segments=tuple(segments),
        words=tuple(words),
        backend="redux",
        model=model,
        device=device,
        load_seconds=load_seconds,
        transcription_seconds=max(0.0, transcription_seconds),
    )


def _parse_word(value: object, label: str) -> TranscriptWord:
    word_value = _field(value, "text", _field(value, "word", None))
    if not isinstance(word_value, str) or not word_value.strip():
        raise ReduxTimestampError(f"{label}.word must be a non-empty string")
    start, end = _interval(value, label=label)
    return TranscriptWord(text=word_value, start_seconds=start, end_seconds=end)


class ReduxSession:
    """One reusable Photon model session for a verification operation or matrix."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_REDUX_MODEL,
        device: str = "cpu",
    ) -> None:
        self.model = model
        self.device = device
        self.load_seconds: float | None = None
        self._manager: Any = None
        self._speech: Any = None

    def __enter__(self) -> Self:
        try:
            import moondream as md
        except ImportError as error:
            raise ReduxIntegrationError(
                "Redux verification requires the optional dependency; install readio[verification]"
            ) from error
        photon = getattr(md, "photon", None)
        if not callable(photon):
            raise ReduxIntegrationError("installed moondream package does not expose photon()")
        started = perf_counter()
        try:
            self._manager = photon(self.model, device=self.device)
            self._speech = self._manager.__enter__()
        except Exception as error:
            self._manager = None
            raise ReduxIntegrationError(f"could not initialize Redux: {error}") from error
        self.load_seconds = perf_counter() - started
        return self

    def transcribe(
        self,
        audio: Path,
        *,
        timestamps: TimestampMode = "word",
    ) -> TranscriptionResult:
        if self._speech is None:
            raise RuntimeError("ReduxSession must be entered before transcription")
        started = perf_counter()
        try:
            raw = self._speech.transcribe(audio=Path(audio), timestamps=timestamps)
        except Exception as error:
            raise ReduxIntegrationError(f"Redux transcription failed: {error}") from error
        elapsed = perf_counter() - started
        return parse_transcription(
            raw,
            model=self.model,
            device=self.device,
            load_seconds=self.load_seconds,
            transcription_seconds=elapsed,
            timestamps=timestamps,
        )

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        manager, self._manager, self._speech = self._manager, None, None
        if manager is not None:
            return manager.__exit__(exc_type, exc, traceback)
        return None


__all__ = [
    "DEFAULT_REDUX_MODEL",
    "ReduxIntegrationError",
    "ReduxSession",
    "ReduxTimestampError",
    "TimestampMode",
    "moondream_version",
    "parse_transcription",
]
