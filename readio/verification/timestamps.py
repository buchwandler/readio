"""Timestamp structure validation and report-only native timing comparison."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from statistics import median
from typing import Any, cast

from ..engines.base import SpeechWordTiming, validate_word_timings
from ..verification.alignment import AlignmentResult, align_words
from ..verification.text import normalize_text
from ..verification.types import TranscriptionResult, TranscriptWord

TIMESTAMP_ARTIFACT_FORMAT = "readio.verification.timestamps"
TIMESTAMP_ARTIFACT_SCHEMA_VERSION = 1
ALIGNMENT_SCHEMA = "readio.source-alignment.v1"


class TimestampValidationError(ValueError):
    """Audio or word timing data violates Readio's shared timing invariants."""


def _numeric_summary(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    p95 = ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
    return {
        "median": float(median(ordered)),
        "p95": p95,
        "max": ordered[-1],
    }


def timings_from_mappings(values: Sequence[Mapping[str, Any]]) -> tuple[SpeechWordTiming, ...]:
    result: list[SpeechWordTiming] = []
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            raise TimestampValidationError(f"native word timing {index} must be an object")
        text = value.get("text")
        coordinates = (
            value.get("char_start"),
            value.get("char_end"),
            value.get("start_sample"),
            value.get("end_sample"),
        )
        if not isinstance(text, str):
            raise TimestampValidationError(f"native word timing {index} has non-string text")
        if any(isinstance(item, bool) or not isinstance(item, int) for item in coordinates):
            raise TimestampValidationError(
                f"native word timing {index} coordinates must be integers"
            )
        result.append(
            SpeechWordTiming(
                text=text,
                char_start=cast(int, coordinates[0]),
                char_end=cast(int, coordinates[1]),
                start_sample=cast(int, coordinates[2]),
                end_sample=cast(int, coordinates[3]),
            )
        )
    return tuple(result)


def _transcription_words(transcription: TranscriptionResult) -> tuple[TranscriptWord, ...]:
    if transcription.words:
        return transcription.words
    return tuple(word for segment in transcription.segments for word in segment.words)


def compare_timestamps(
    *,
    text: str,
    transcription: TranscriptionResult,
    sample_rate: int,
    frames: int,
    native_timings: Sequence[Mapping[str, Any]] = (),
    context: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Align ASR words to source spans, validate both timing sets, and report accuracy."""
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate <= 0:
        raise TimestampValidationError("sample rate must be a positive integer")
    if isinstance(frames, bool) or not isinstance(frames, int) or frames <= 0:
        raise TimestampValidationError("audio frame count must be a positive integer")
    words = _transcription_words(transcription)
    if not isinstance(native_timings, Sequence) or isinstance(
        native_timings, (str, bytes, bytearray)
    ):
        raise TimestampValidationError("native word timings must be a sequence")
    duration = frames / sample_rate
    previous_start = previous_end = -1.0
    for index, word in enumerate(words):
        start_value, end_value = word.start_seconds, word.end_seconds
        if (
            isinstance(start_value, bool)
            or isinstance(end_value, bool)
            or not isinstance(start_value, (int, float))
            or not isinstance(end_value, (int, float))
            or not math.isfinite(float(start_value))
            or not math.isfinite(float(end_value))
        ):
            raise TimestampValidationError(f"ASR word {index} times must be finite numeric seconds")
        start_seconds, end_seconds = float(start_value), float(end_value)
        if start_seconds < 0 or end_seconds < start_seconds or end_seconds > duration:
            raise TimestampValidationError(
                f"ASR word {index} interval is outside the audio duration"
            )
        if start_seconds < previous_start or end_seconds < previous_end:
            raise TimestampValidationError("ASR word timestamps are not monotonic")
        previous_start, previous_end = start_seconds, end_seconds
    alignment: AlignmentResult = align_words(text, words)
    derived: list[SpeechWordTiming] = []
    for operation in alignment.operations:
        if operation.kind != "equal" or operation.source is None or operation.recognized is None:
            continue
        start_sample = round(operation.recognized.start_seconds * sample_rate)
        end_sample = round(operation.recognized.end_seconds * sample_rate)
        derived.append(
            SpeechWordTiming(
                text=operation.source.raw,
                char_start=operation.source.char_start,
                char_end=operation.source.char_end,
                start_sample=start_sample,
                end_sample=end_sample,
            )
        )
    try:
        derived_valid = validate_word_timings(
            text=text,
            frame_count=frames,
            timings=tuple(derived),
            context=context,
        )
        native_valid = validate_word_timings(
            text=text,
            frame_count=frames,
            timings=timings_from_mappings(native_timings),
            context=context,
        )
    except ValueError as error:
        raise TimestampValidationError(str(error)) from error

    source_by_span = {
        (word.char_start, word.char_end): word.normalized for word in alignment.source_words
    }
    native_by_span = {
        (timing.char_start, timing.char_end): timing
        for timing in native_valid
        if source_by_span.get((timing.char_start, timing.char_end)) == normalize_text(timing.text)
    }
    start_errors: list[float] = []
    end_errors: list[float] = []
    paired = 0
    for asr_timing in derived_valid:
        reference = native_by_span.get((asr_timing.char_start, asr_timing.char_end))
        if reference is None:
            continue
        paired += 1
        start_errors.append(
            abs(asr_timing.start_sample - reference.start_sample) * 1000 / sample_rate
        )
        end_errors.append(abs(asr_timing.end_sample - reference.end_sample) * 1000 / sample_rate)
    expected_words = alignment.expected_words
    return {
        "alignment": {
            "expected_words": expected_words,
            "recognized_words": alignment.recognized_count,
            "matched_words": alignment.matched_words,
            "coverage": alignment.coverage,
            "substitutions": alignment.substitutions,
            "deletions": alignment.deletions,
            "insertions": alignment.insertions,
        },
        "timing_structure": {
            "status": "pass" if words else "unavailable",
            "asr_word_timings": len(words),
            "derived_word_timings": len(derived_valid),
            "native_word_timings": len(native_valid),
        },
        "native_comparison": {
            "status": "available" if native_valid else "unavailable",
            "paired_words": paired,
            "coverage": paired / expected_words if expected_words else 0.0,
            "start_error_ms": _numeric_summary(start_errors),
            "end_error_ms": _numeric_summary(end_errors),
        },
        "derived_word_timings": [
            {
                "text": timing.text,
                "char_start": timing.char_start,
                "char_end": timing.char_end,
                "start_sample": timing.start_sample,
                "end_sample": timing.end_sample,
            }
            for timing in derived_valid
        ],
        "operations": [
            {
                "kind": operation.kind,
                "source": (
                    {
                        "raw": operation.source.raw,
                        "normalized": operation.source.normalized,
                        "char_start": operation.source.char_start,
                        "char_end": operation.source.char_end,
                    }
                    if operation.source is not None
                    else None
                ),
                "recognized": (
                    {
                        "raw": operation.recognized.raw,
                        "normalized": operation.recognized.normalized,
                        "start_seconds": operation.recognized.start_seconds,
                        "end_seconds": operation.recognized.end_seconds,
                        "word_index": operation.recognized.word_index,
                    }
                    if operation.recognized is not None
                    else None
                ),
            }
            for operation in alignment.operations
        ],
    }
