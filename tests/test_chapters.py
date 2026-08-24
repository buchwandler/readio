import json
import subprocess
from pathlib import Path

import pytest

from readio import spotify


def test_build_timeline_converts_global_sample_offsets():
    timeline = spotify.build_timeline(
        (
            {"name": "intro", "sample_offset": 0},
            {"name": "topic", "sample_offset": 24000},
        ),
        24000,
    )

    assert timeline == {
        "items": [
            {"chapter": {"title": "intro", "start_time_ms": 0}},
            {"chapter": {"title": "topic", "start_time_ms": 1000}},
        ]
    }


@pytest.mark.parametrize(
    "markers, message",
    [
        (({"name": "only", "sample_offset": 0},), "at least two"),
        (({"name": "intro", "sample_offset": 1}, {"name": "topic", "sample_offset": 2}), "first"),
        (
            ({"name": "intro", "sample_offset": 0}, {"name": "topic", "sample_offset": 0}),
            "strictly increasing",
        ),
    ],
)
def test_invalid_chapter_sets_fail_before_timeline_call(markers, message):
    with pytest.raises(spotify.SpotifyError, match=message):
        spotify.build_timeline(markers, 24000)


def test_set_timeline_uses_json_file_argument(monkeypatch, tmp_path: Path):
    calls = []
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps({"items": []}), encoding="utf-8")
    monkeypatch.setattr(spotify.shutil, "which", lambda name: "/bin/save-to-spotify")
    monkeypatch.setattr(
        spotify.subprocess,
        "run",
        lambda command, **kwargs: (
            calls.append((command, kwargs)) or subprocess.CompletedProcess([], 0, "{}", "")
        ),
    )

    spotify.set_timeline("spotify:episode:1", path)

    assert calls == [
        (
            [
                "/bin/save-to-spotify",
                "--json",
                "timeline",
                "set",
                "--episode-id",
                "spotify:episode:1",
                "--from-file",
                str(path),
            ],
            {"capture_output": True, "text": True, "check": False},
        )
    ]
