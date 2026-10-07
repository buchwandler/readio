from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

from readio.verification.voice_matrix import (
    VOICE_MATRIX_SCHEMA,
    filter_runnable_voices,
    summarize_voice_results,
    write_voice_matrix,
)


def test_filter_runnable_voices_preserves_order_and_excludes_experimental_by_default():
    voices = (
        SimpleNamespace(runtime_available=True, experimental=False, id="first"),
        SimpleNamespace(runtime_available=False, experimental=False, id="unavailable"),
        SimpleNamespace(runtime_available=True, experimental=True, id="experimental"),
    )
    assert [voice.id for voice in filter_runnable_voices(voices)] == ["first"]
    assert [voice.id for voice in filter_runnable_voices(voices, include_experimental=True)] == [
        "first",
        "experimental",
    ]


def test_voice_matrix_summary_and_stable_transcript_free_csv(tmp_path: Path):
    rows = [
        {
            "voice": {
                "ref": "kokoro:v1/voice",
                "id": "voice",
                "engine": "kokoro",
                "model": "v1",
                "language": "en",
            },
            "status": "pass",
            "planning": {"status": "repaired"},
            "synthesis_status": "pass",
            "verification": {
                "status": "pass",
                "wer": 0.0,
                "cer": 0.0,
                "transcript": "private transcript",
            },
            "composition": {"audio_seconds": 2.0, "wav_sha256": "abc", "wav": "master.wav"},
            "timings": {"synthesis_seconds": 1.0, "redux_seconds": 0.5},
        },
        {
            "voice": {
                "ref": "kokoro:v1/other",
                "id": "other",
                "engine": "kokoro",
                "model": "v1",
                "language": "en",
            },
            "status": "review",
            "verification": {"status": "review", "wer": 0.1, "cer": 0.05},
        },
    ]
    summary = summarize_voice_results(rows)
    assert summary["voice_count"] == 2
    assert summary["passed"] == 1
    assert summary["review"] == 1
    assert summary["wer"] == {"median": 0.05, "min": 0.0, "max": 0.1}
    document = {
        "schema": VOICE_MATRIX_SCHEMA,
        "summary": summary,
        "results": rows,
    }
    write_voice_matrix(tmp_path, document)
    saved = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert saved["schema"] == VOICE_MATRIX_SCHEMA
    with (tmp_path / "results.csv").open(encoding="utf-8", newline="") as stream:
        csv_rows = list(csv.DictReader(stream))
    assert csv_rows[0]["voice_ref"] == "kokoro:v1/voice"
    assert csv_rows[0]["synthesis_x_real_time"] == "2.0"
    assert "transcript" not in csv_rows[0]
    assert "private transcript" not in (tmp_path / "results.csv").read_text(encoding="utf-8")
