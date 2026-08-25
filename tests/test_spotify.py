import json
import subprocess
from pathlib import Path

import pytest

from readio import spotify


def completed(payload, returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, json.dumps(payload), stderr)


def test_upload_builds_json_command_with_optional_metadata(monkeypatch, tmp_path: Path):
    calls = []
    monkeypatch.setattr(spotify.shutil, "which", lambda name: "/bin/save-to-spotify")
    monkeypatch.setattr(
        spotify.subprocess,
        "run",
        lambda command, **kwargs: (
            calls.append((command, kwargs))
            or completed({"episode_uri": "spotify:episode:1", "status": "UPLOADING"})
        ),
    )

    result = spotify.upload_episode(
        tmp_path / "episode with spaces.wav",
        title="Episode",
        show_id="spotify:show:1",
        summary="Summary",
        image=tmp_path / "cover art.png",
        language="en",
    )

    assert result.episode_uri == "spotify:episode:1"
    assert calls == [
        (
            [
                "/bin/save-to-spotify",
                "--json",
                "upload",
                str(tmp_path / "episode with spaces.wav"),
                "--title",
                "Episode",
                "--show-id",
                "spotify:show:1",
                "--summary",
                "Summary",
                "--image",
                str(tmp_path / "cover art.png"),
                "--language",
                "en",
            ],
            {"capture_output": True, "text": True, "check": False},
        )
    ]


def test_upload_parses_errors_and_protocol_failures(monkeypatch):
    monkeypatch.setattr(spotify.shutil, "which", lambda name: "/bin/save-to-spotify")
    monkeypatch.setattr(
        spotify.subprocess,
        "run",
        lambda *args, **kwargs: completed({"error": "not authenticated"}, returncode=3),
    )
    with pytest.raises(spotify.SpotifyError, match="not authenticated"):
        spotify.upload_episode(Path("episode.wav"), title="Episode")

    monkeypatch.setattr(
        spotify.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, "not json", ""),
    )
    with pytest.raises(spotify.SpotifyError, match="invalid JSON"):
        spotify.upload_episode(Path("episode.wav"), title="Episode")


def test_wait_for_episode_supports_custom_timeout(monkeypatch):
    commands = []
    monkeypatch.setattr(spotify.shutil, "which", lambda name: "/bin/save-to-spotify")
    monkeypatch.setattr(
        spotify.subprocess,
        "run",
        lambda command, **kwargs: (
            commands.append(command)
            or completed({"episode_uri": "spotify:episode:1", "readiness": "READY"})
        ),
    )

    result = spotify.wait_for_episode("spotify:episode:1", timeout="2m")

    assert result.readiness == "READY"
    assert commands == [
        [
            "/bin/save-to-spotify",
            "--json",
            "episodes",
            "status",
            "spotify:episode:1",
            "--wait",
            "2m",
        ]
    ]


def test_missing_executable_is_actionable(monkeypatch):
    monkeypatch.setattr(spotify.shutil, "which", lambda name: None)
    with pytest.raises(spotify.SpotifyError, match="not found on PATH"):
        spotify.upload_episode(Path("episode.wav"), title="Episode")


def test_upload_accepts_non_wav_media_path(monkeypatch, tmp_path: Path):
    calls = []
    monkeypatch.setattr(spotify.shutil, "which", lambda name: "/bin/save-to-spotify")
    monkeypatch.setattr(
        spotify.subprocess,
        "run",
        lambda command, **kwargs: (
            calls.append(command)
            or completed({"episode_uri": "spotify:episode:2", "status": "UPLOADING"})
        ),
    )

    path = tmp_path / "episode.mp3"
    result = spotify.upload_episode(path, title="Episode")

    assert result.episode_uri == "spotify:episode:2"
    assert str(path) in calls[0]


def test_version_probe_and_api_timeout_forwarding(monkeypatch):
    commands = []
    monkeypatch.setattr(spotify.shutil, "which", lambda name: "/bin/save-to-spotify")
    monkeypatch.setattr(
        spotify.subprocess,
        "run",
        lambda command, **kwargs: (
            commands.append(command) or completed({"version": "0.2.0", "commit": "abc"})
        ),
    )

    result = spotify.version(api_timeout="10s")

    assert result.version == "0.2.0"
    assert result.commit == "abc"
    assert commands == [["/bin/save-to-spotify", "--json", "--timeout", "10s", "version"]]


def test_list_shows_and_status_accept_ids_and_uris(monkeypatch):
    commands = []
    monkeypatch.setattr(spotify.shutil, "which", lambda name: "/bin/save-to-spotify")
    responses = iter(
        [
            {"shows": [{"uri": "spotify:show:1", "title": "Briefings"}]},
            {"readiness": "READY", "future_field": "ignored"},
        ]
    )
    monkeypatch.setattr(
        spotify.subprocess,
        "run",
        lambda command, **kwargs: commands.append(command) or completed(next(responses)),
    )

    shows = spotify.list_shows()
    status = spotify.episode_status("abc", wait=True)

    assert shows[0].show_uri == "spotify:show:1"
    assert status.episode_uri == "abc"
    assert status.readiness == "READY"
    assert commands[0] == ["/bin/save-to-spotify", "--json", "shows"]
    assert commands[1] == ["/bin/save-to-spotify", "--json", "episodes", "status", "abc", "--wait"]
