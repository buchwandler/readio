import json
from pathlib import Path

import pytest

from readio import cli
from readio.audio import RenderSummary
from readio.spotify import SpotifyReadinessResult, SpotifyUploadResult


def test_spotify_cleans_temporary_audio_after_upload(monkeypatch):
    uploaded_paths = []

    def render(args, path, *, audio_format):
        assert audio_format == "mp3"
        assert path.suffix == ".mp3"
        path.write_bytes(b"mp3")

    def upload(path, **kwargs):
        assert path.exists()
        uploaded_paths.append(path)
        return SpotifyUploadResult("spotify:episode:1", "UPLOADING")

    monkeypatch.setattr(cli, "_render_audio", render)
    monkeypatch.setattr(cli, "upload_episode", upload)
    args = cli.build_parser().parse_args(
        ["spotify", "text", "--title", "Episode", "--format", "mp3"]
    )

    assert cli._cmd_spotify(args) == 0
    assert len(uploaded_paths) == 1
    assert uploaded_paths[0].suffix == ".mp3"
    assert not uploaded_paths[0].exists()


def test_spotify_keeps_persistent_audio_and_waits(monkeypatch, tmp_path: Path):
    output = tmp_path / "episode.wav"
    calls = []

    monkeypatch.setattr(
        cli,
        "_render_audio",
        lambda args, path, *, audio_format: path.write_bytes(b"wav"),
    )

    def upload(path, **kwargs):
        assert path == output
        assert path.exists()
        return SpotifyUploadResult("spotify:episode:1", "UPLOADING")

    def wait(uri, *, timeout):
        calls.append((uri, timeout))
        return SpotifyReadinessResult(uri, "READY")

    monkeypatch.setattr(cli, "upload_episode", upload)
    monkeypatch.setattr(cli, "wait_for_episode", wait)
    args = cli.build_parser().parse_args(
        [
            "spotify",
            "text",
            "--title",
            "Episode",
            "--output",
            str(output),
            "--wait",
            "--wait-timeout",
            "2m",
        ]
    )

    assert cli._cmd_spotify(args) == 0
    assert output.read_bytes() == b"wav"
    assert calls == [("spotify:episode:1", "2m")]


def test_spotify_persistent_output_infers_format(monkeypatch, tmp_path: Path):
    output = tmp_path / "episode.ogg"
    rendered = []
    monkeypatch.setattr(
        cli,
        "_render_audio",
        lambda args, path, *, audio_format: (
            rendered.append((path, audio_format)) or path.write_bytes(b"ogg")
        ),
    )
    monkeypatch.setattr(
        cli,
        "upload_episode",
        lambda path, **kwargs: SpotifyUploadResult("spotify:episode:1", "UPLOADING"),
    )
    args = cli.build_parser().parse_args(
        ["spotify", "text", "--title", "Episode", "--output", str(output)]
    )

    assert cli._cmd_spotify(args) == 0
    assert rendered[0][1] == "ogg"
    assert rendered[0][0].suffix == ".ogg"
    assert output.read_bytes() == b"ogg"


def test_spotify_does_not_upload_when_render_fails(monkeypatch):
    def render_failure(args, path, *, audio_format):
        raise RuntimeError("render failed")

    monkeypatch.setattr(cli, "_render_audio", render_failure)
    monkeypatch.setattr(cli, "upload_episode", lambda *args, **kwargs: pytest.fail("upload called"))
    args = cli.build_parser().parse_args(["spotify", "text", "--title", "Episode"])

    with pytest.raises(RuntimeError, match="render failed"):
        cli._cmd_spotify(args)


def test_marker_chapters_wait_and_set_timeline(monkeypatch, capsys):
    def render(args, path, *, audio_format):
        path.write_bytes(b"wav")
        return RenderSummary(
            sample_rate=24000,
            sample_count=48000,
            channels=1,
            markers=(
                {"name": "intro", "sample_offset": 0},
                {"name": "topic", "sample_offset": 24000},
            ),
        )

    timeline_calls = []
    monkeypatch.setattr(cli, "_render_audio", render)
    monkeypatch.setattr(
        cli,
        "upload_episode",
        lambda path, **kwargs: SpotifyUploadResult("spotify:episode:1", "UPLOADING"),
    )
    monkeypatch.setattr(
        cli,
        "wait_for_episode",
        lambda uri, *, timeout: SpotifyReadinessResult(uri, "READY"),
    )

    def set_timeline(uri, path):
        timeline_calls.append((uri, json.loads(path.read_text(encoding="utf-8"))))
        assert path.exists()

    monkeypatch.setattr(cli, "set_timeline", set_timeline)
    args = cli.build_parser().parse_args(
        ["spotify", "text", "--title", "Episode", "--chapters-from-markers", "--json"]
    )

    assert cli._cmd_spotify(args) == 0
    assert timeline_calls == [
        (
            "spotify:episode:1",
            {
                "items": [
                    {"chapter": {"title": "intro", "start_time_ms": 0}},
                    {"chapter": {"title": "topic", "start_time_ms": 1000}},
                ]
            },
        )
    ]
    result = json.loads(capsys.readouterr().out)
    assert result["readiness"] == "READY"
    assert result["audio_format"] == "wav"


def test_spotify_json_explicit_progress_stays_on_stderr(monkeypatch, capsys):
    def render(args, path, *, audio_format, **kwargs):
        path.write_bytes(b"wav")
        return RenderSummary(sample_rate=24000, sample_count=24000, channels=1)

    monkeypatch.setattr(cli, "_render_audio", render)
    monkeypatch.setattr(
        cli,
        "upload_episode",
        lambda path, **kwargs: SpotifyUploadResult("spotify:episode:2", "UPLOADING"),
    )
    args = cli.build_parser().parse_args(
        ["spotify", "text", "--title", "Episode", "--json", "--progress"]
    )

    assert cli._cmd_spotify(args) == 0

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["episode_uri"] == "spotify:episode:2"
    assert "Preparing" in captured.err
    assert "Uploading" in captured.err
