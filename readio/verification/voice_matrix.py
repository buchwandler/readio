"""Model-free helpers for stable Redux voice-matrix artifacts."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import median
from typing import Any

from ..jsonutil import json_value

VOICE_MATRIX_SCHEMA = "readio.verification.voice-matrix.v1"
CSV_COLUMNS = (
    "voice_ref",
    "voice_id",
    "engine",
    "model",
    "language",
    "status",
    "planning_status",
    "synthesis_status",
    "verification_status",
    "wer",
    "cer",
    "audio_seconds",
    "synthesis_seconds",
    "redux_seconds",
    "synthesis_x_real_time",
    "redux_x_real_time",
    "wav_sha256",
    "wav_path",
    "error",
)


def filter_runnable_voices(
    voices: Sequence[Any], *, include_experimental: bool = False
) -> tuple[Any, ...]:
    """Keep runnable catalog entries in canonical catalog order."""
    return tuple(
        voice
        for voice in voices
        if bool(getattr(voice, "runtime_available", False))
        and (include_experimental or not bool(getattr(voice, "experimental", False)))
    )


def _metric_summary(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {
        "median": float(median(values)),
        "min": min(values),
        "max": max(values),
    }


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def summarize_voice_results(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = {"pass": 0, "review": 0, "fail": 0}
    for row in rows:
        status = row.get("status")
        if status in counts:
            counts[str(status)] += 1
    verifications: list[Mapping[str, Any]] = []
    for row in rows:
        verification = row.get("verification")
        if isinstance(verification, Mapping):
            verifications.append(verification)
    wer = [
        float(item["wer"]) for item in verifications if isinstance(item.get("wer"), (int, float))
    ]
    cer = [
        float(item["cer"]) for item in verifications if isinstance(item.get("cer"), (int, float))
    ]
    return {
        "voice_count": len(rows),
        "passed": counts["pass"],
        "review": counts["review"],
        "failed": counts["fail"],
        "wer": _metric_summary(wer),
        "cer": _metric_summary(cer),
    }


def write_voice_matrix(output: Path, document: Mapping[str, Any]) -> None:
    """Persist stable JSON and transcript-free CSV summary artifacts."""
    output.mkdir(parents=True, exist_ok=True)
    clean_document = json_value(document)
    (output / "results.json").write_text(
        json.dumps(clean_document, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    with (output / "results.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in document.get("results", ()):
            if not isinstance(row, Mapping):
                continue
            voice = _as_mapping(row.get("voice"))
            planning = _as_mapping(row.get("planning"))
            verification = _as_mapping(row.get("verification"))
            composition = _as_mapping(row.get("composition"))
            timings = _as_mapping(row.get("timings"))
            audio_seconds = composition.get("audio_seconds")
            synth_seconds = timings.get("synthesis_seconds")
            redux_seconds = timings.get("redux_seconds")
            writer.writerow(
                {
                    "voice_ref": voice.get("ref"),
                    "voice_id": voice.get("id"),
                    "engine": voice.get("engine"),
                    "model": voice.get("model"),
                    "language": voice.get("language"),
                    "status": row.get("status"),
                    "planning_status": planning.get("status"),
                    "synthesis_status": row.get("synthesis_status"),
                    "verification_status": verification.get("status"),
                    "wer": verification.get("wer"),
                    "cer": verification.get("cer"),
                    "audio_seconds": audio_seconds,
                    "synthesis_seconds": synth_seconds,
                    "redux_seconds": redux_seconds,
                    "synthesis_x_real_time": (
                        float(audio_seconds) / float(synth_seconds)
                        if isinstance(audio_seconds, (int, float))
                        and isinstance(synth_seconds, (int, float))
                        and synth_seconds > 0
                        else None
                    ),
                    "redux_x_real_time": (
                        float(audio_seconds) / float(redux_seconds)
                        if isinstance(audio_seconds, (int, float))
                        and isinstance(redux_seconds, (int, float))
                        and redux_seconds > 0
                        else None
                    ),
                    "wav_sha256": composition.get("wav_sha256"),
                    "wav_path": composition.get("wav"),
                    "error": json.dumps(row.get("error"), sort_keys=True)
                    if row.get("error")
                    else None,
                }
            )
