import json
from pathlib import Path

import pytest

from readio import cli, spotify_cli
from readio.audio import RenderSummary
from readio.config import PathSettings, ReadioConfig
from readio.spotify import SpotifyReadinessResult, SpotifyUploadResult


def _configure(monkeypatch, tmp_path: Path) -> None:
    cfg = ReadioConfig(
        paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "output")
    )
    monkeypatch.setattr(cli, "_resolved_config", lambda args: cfg)
    monkeypatch.setattr(cli, "ensure_audio_format_available", lambda audio_format: None)


def test_publish_cleans_temporary_audio_after_upload(monkeypatch, tmp_path: Path):
    _configure(monkeypatch, tmp_path)
    uploaded_paths: list[Path] = []

    def render(args, path, *, audio_format, **kwargs):
        assert audio_format == "mp3"
        path.write_bytes(b"mp3")
        return RenderSummary(sample_rate=24000, sample_count=24000, channels=1)

    def upload(path, **kwargs):
        assert path.exists()
        uploaded_paths.append(path)
        return SpotifyUploadResult("spotify:episode:1", "UPLOADING")

    monkeypatch.setattr(cli, "_render_audio", render)
    monkeypatch.setattr(spotify_cli, "upload_episode", upload)
    args = cli.build_parser().parse_args(
        ["spotify", "publish", "text", "--title", "Episode", "--format", "mp3"]
    )

    assert spotify_cli.cmd_spotify_publish(args) == 0
    assert len(uploaded_paths) == 1
    assert not uploaded_paths[0].exists()


def test_publish_keeps_persistent_audio_and_waits(monkeypatch, tmp_path: Path):
    _configure(monkeypatch, tmp_path)
    output = tmp_path / "episode.wav"
    calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr(
        cli,
        "_render_audio",
        lambda args, path, *, audio_format, **kwargs: (
            path.write_bytes(b"wav") and RenderSummary(24000, 24000, 1)
        ),
    )

    def upload(path, **kwargs):
        assert path == output
        return SpotifyUploadResult("spotify:episode:1", "UPLOADING")

    def wait(uri, *, wait, wait_timeout, api_timeout):
        calls.append((uri, wait_timeout))
        return SpotifyReadinessResult(uri, "READY")

    monkeypatch.setattr(spotify_cli, "upload_episode", upload)
    monkeypatch.setattr(spotify_cli, "episode_status", wait)
    args = cli.build_parser().parse_args(
        [
            "spotify",
            "publish",
            "text",
            "--title",
            "Episode",
            "--output",
            str(output),
            "--wait",
            "2m",
        ]
    )

    assert spotify_cli.cmd_spotify_publish(args) == 0
    assert output.read_bytes() == b"wav"
    assert calls == [("spotify:episode:1", "2m")]


def test_upload_never_renders_or_deletes_caller_media(monkeypatch, tmp_path: Path):
    media = tmp_path / "recording.m4a"
    media.write_bytes(b"m4a")
    monkeypatch.setattr(cli, "_render_audio", lambda *args, **kwargs: pytest.fail("rendered"))
    monkeypatch.setattr(
        spotify_cli,
        "upload_episode",
        lambda path, **kwargs: SpotifyUploadResult("spotify:episode:2", "PROCESSING"),
    )
    args = cli.build_parser().parse_args(
        ["spotify", "upload", str(media), "--title", "Lecture", "--json"]
    )

    assert spotify_cli.cmd_spotify_upload(args) == 0
    assert media.read_bytes() == b"m4a"


def test_marker_timeline_waits_then_sets_and_cleans_temp(monkeypatch, tmp_path: Path):
    _configure(monkeypatch, tmp_path)
    timeline_calls: list[tuple[str, dict[str, object], bool]] = []
    monkeypatch.setattr(
        cli,
        "_render_audio",
        lambda args, path, *, audio_format, **kwargs: (
            path.write_bytes(b"wav")
            and RenderSummary(
                24000,
                48000,
                1,
                markers=(
                    {"name": "intro", "sample_offset": 0},
                    {"name": "topic", "sample_offset": 24000},
                ),
            )
        ),
    )
    monkeypatch.setattr(
        spotify_cli,
        "upload_episode",
        lambda path, **kwargs: SpotifyUploadResult("spotify:episode:3", "PROCESSING"),
    )
    monkeypatch.setattr(
        spotify_cli,
        "episode_status",
        lambda uri, **kwargs: SpotifyReadinessResult(uri, "READY"),
    )

    def set_timeline(uri, path, **kwargs):
        timeline_calls.append((uri, json.loads(path.read_text()), path.exists()))

    monkeypatch.setattr(spotify_cli, "set_timeline", set_timeline)
    args = cli.build_parser().parse_args(
        ["spotify", "publish", "text", "--title", "Episode", "--chapters-from-markers", "--json"]
    )

    assert spotify_cli.cmd_spotify_publish(args) == 0
    assert timeline_calls[0][0] == "spotify:episode:3"
    assert timeline_calls[0][1]["items"][1]["chapter"]["start_time_ms"] == 1000
    assert timeline_calls[0][2]


def test_caller_timeline_is_untouched(monkeypatch, tmp_path: Path):
    media = tmp_path / "recording.mp3"
    media.write_bytes(b"mp3")
    timeline = tmp_path / "timeline.json"
    original = '{"items": [{"chapter": {"title": "One"}}]}'
    timeline.write_text(original)
    monkeypatch.setattr(
        spotify_cli,
        "upload_episode",
        lambda path, **kwargs: SpotifyUploadResult("spotify:episode:4", "PROCESSING"),
    )
    monkeypatch.setattr(
        spotify_cli,
        "episode_status",
        lambda uri, **kwargs: SpotifyReadinessResult(uri, "READY"),
    )
    observed: list[Path] = []
    monkeypatch.setattr(
        spotify_cli, "set_timeline", lambda uri, path, **kwargs: observed.append(path)
    )
    args = cli.build_parser().parse_args(
        [
            "spotify",
            "upload",
            str(media),
            "--title",
            "Episode",
            "--timeline",
            str(timeline),
        ]
    )

    assert spotify_cli.cmd_spotify_upload(args) == 0
    assert observed == [timeline]
    assert timeline.read_text() == original


def test_timeline_modes_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            [
                "spotify",
                "publish",
                "text",
                "--title",
                "Episode",
                "--timeline",
                "timeline.json",
                "--chapters-from-markers",
            ]
        )
