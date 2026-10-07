from __future__ import annotations

import math
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from readio.integrations.moondream import (
    ReduxSession,
    ReduxTimestampError,
    parse_transcription,
)
from readio.verification.types import TranscriptSegment, TranscriptWord


def test_parse_nested_segments_and_flatten_words() -> None:
    result = parse_transcription(
        {
            "text": "hello there",
            "segments": [
                {
                    "start": 0.0,
                    "end": 0.5,
                    "text": "hello there",
                    "words": [
                        {"word": "hello", "start": 0.0, "end": 0.2},
                        {"word": "there", "start": 0.21, "end": 0.5},
                    ],
                }
            ],
        }
    )
    assert result.text == "hello there"
    assert result.segments == (
        TranscriptSegment(
            "hello there",
            0.0,
            0.5,
            (TranscriptWord("hello", 0.0, 0.2), TranscriptWord("there", 0.21, 0.5)),
        ),
    )
    assert result.words == result.segments[0].words


@pytest.mark.parametrize(
    "key",
    ["words", "timestamps"],
)
def test_parse_legacy_aggregate_timing_shapes(key: str) -> None:
    result = parse_transcription(
        {"text": "hello", key: [{"text": "hello", "start": 0, "end": 0.2}]}
    )
    assert result.text == "hello"
    assert result.words == (TranscriptWord("hello", 0.0, 0.2),)


@pytest.mark.parametrize(
    "record",
    [
        {"text": "bad", "start": -1, "end": 0},
        {"text": "bad", "start": 0, "end": float("nan")},
        {"text": "bad", "start": 0, "end": float("inf")},
        {"text": "bad", "start": 1, "end": 0},
        {"text": "bad", "start": "zero", "end": 1},
    ],
)
def test_rejects_invalid_word_timestamps(record: dict[str, Any]) -> None:
    with pytest.raises(ReduxTimestampError):
        parse_transcription({"text": "bad", "words": [record]})


def test_rejects_backwards_and_out_of_segment_timestamps() -> None:
    with pytest.raises(ReduxTimestampError, match="backwards"):
        parse_transcription(
            {
                "segments": [
                    {
                        "start": 0,
                        "end": 2,
                        "words": [
                            {"text": "one", "start": 1, "end": 1.2},
                            {"text": "two", "start": 0.5, "end": 0.8},
                        ],
                    }
                ]
            }
        )
    with pytest.raises(ReduxTimestampError, match="outside"):
        parse_transcription(
            {
                "segments": [
                    {"start": 0, "end": 1, "words": [{"text": "bad", "start": 0.8, "end": 1.2}]}
                ]
            }
        )


def test_redux_session_parses_nested_result_and_reuses_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []

    class Speech:
        def transcribe(self, *, audio: Path, timestamps: str) -> dict[str, object]:
            calls.append((audio, timestamps))
            return {
                "text": "hello",
                "segments": [
                    {
                        "start": 0,
                        "end": 0.2,
                        "text": "hello",
                        "words": [{"word": "hello", "start": 0, "end": 0.2}],
                    }
                ],
            }

    class Manager:
        def __enter__(self) -> Speech:
            return Speech()

        def __exit__(self, *_args: object) -> None:
            calls.append("closed")

    fake = ModuleType("moondream")
    fake.__dict__["photon"] = lambda model, *, device: Manager()
    monkeypatch.setitem(__import__("sys").modules, "moondream", fake)
    audio = tmp_path / "audio.wav"
    with ReduxSession() as session:
        result = session.transcribe(audio)
    assert result.words == (TranscriptWord("hello", 0.0, 0.2),)
    assert calls == [(audio, "word"), "closed"]


def test_timestamp_validation_does_not_coerce_nan_to_zero() -> None:
    with pytest.raises(ReduxTimestampError):
        parse_transcription({"timestamps": [{"text": "bad", "start": math.nan, "end": 1}]})
