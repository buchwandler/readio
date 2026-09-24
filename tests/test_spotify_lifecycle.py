from __future__ import annotations

import io
import json
from pathlib import Path

from readio import cli, spotify_cli
from readio.api import OutputRequest, Readio, RenderResult, RenderSummary, SynthesisRequest
from readio.api.integrations.spotify import (
    SpotifyLivePublishRequest,
    SpotifyPublishResult,
    SpotifyReadinessResult,
    SpotifyService,
)
from readio.api.speech import SpeechService
from readio.config import ReadioConfig


def _result(
    *,
    audio_path: Path | None = None,
    audio_format: str = "wav",
) -> SpotifyPublishResult:
    return SpotifyPublishResult(
        episode_uri="spotify:episode:1",
        upload_status="UPLOADING",
        readiness=SpotifyReadinessResult("spotify:episode:1", "READY"),
        audio_path=audio_path,
        audio_format=audio_format,
        timeline_published=False,
    )


def test_publish_cli_builds_public_request_and_delegates(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    source = tmp_path / "episode.ssmd"
    source.write_text('<div voice="host">Hello.</div>', encoding="utf-8")
    output = tmp_path / "published.mp3"
    app = Readio(ReadioConfig())
    seen = {}

    class Service:
        def __init__(self, passed_app):
            assert passed_app is app

        def publish(self, request, *, on_event=None):
            seen["request"] = request
            seen["handler"] = on_event
            return _result(
                audio_path=output,
                audio_format=request.render.output.requested_format,
            )

    monkeypatch.setattr(spotify_cli, "Readio", lambda: app)
    monkeypatch.setattr(spotify_cli, "SpotifyService", Service)
    args = cli.build_parser().parse_args(
        [
            "spotify",
            "publish",
            str(source),
            "--title",
            "Episode",
            "--output",
            str(output),
            "--format",
            "mp3",
            "--voice",
            "af_sarah",
            "--speed",
            "1.2",
            "--json",
        ]
    )

    assert spotify_cli.cmd_spotify_publish(args) == 0
    request = seen["request"]
    assert request.render.input.document.source_path == source
    assert request.render.output.requested_path == output
    assert request.render.output.requested_format == "mp3"
    assert request.render.synthesis.voice == "af_sarah"
    assert request.render.synthesis.speed == 1.2
    assert seen["handler"] is not None
    assert json.loads(capsys.readouterr().out)["episode_uri"] == "spotify:episode:1"
    assert not hasattr(spotify_cli, "_cli")


def test_upload_cli_delegates_public_request_without_mutating_audio(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    media = tmp_path / "recording.m4a"
    media.write_bytes(b"caller audio")
    app = Readio(ReadioConfig())
    seen = {}

    class Service:
        def __init__(self, passed_app):
            assert passed_app is app

        def upload(self, request):
            seen["request"] = request
            return _result(audio_path=request.audio_path, audio_format="m4a")

    monkeypatch.setattr(spotify_cli, "Readio", lambda: app)
    monkeypatch.setattr(spotify_cli, "SpotifyService", Service)
    args = cli.build_parser().parse_args(
        ["spotify", "upload", str(media), "--title", "Lecture", "--json"]
    )

    assert spotify_cli.cmd_spotify_upload(args) == 0
    assert seen["request"].audio_path == media
    assert media.read_bytes() == b"caller audio"
    payload = json.loads(capsys.readouterr().out)
    assert payload["audio_format"] == "m4a"
    assert payload["audio_path"] is None


def test_publish_live_owns_default_temporary_audio_file(monkeypatch) -> None:
    app = Readio(ReadioConfig())
    lines = iter(("hello\n",))
    summary = RenderSummary(
        sample_rate=24000,
        sample_count=24000,
        channels=1,
        markers=({"name": "intro", "sample_offset": 0},),
    )
    captured = {}

    def render_live_to_file(self, passed_lines, output, **kwargs):
        captured["lines"] = passed_lines
        captured["output"] = output
        assert tuple(passed_lines) == ("hello\n",)
        assert output.requested_path is not None
        output.requested_path.write_bytes(b"audio")
        return RenderResult(
            plan=None,
            summary=summary,
            output_path=output.requested_path,
            audio_format="wav",
        )

    def publish_rendered(self, upload, rendered_summary, *, chapters_from_markers, on_event=None):
        captured["upload"] = upload
        captured["summary"] = rendered_summary
        captured["temporary_exists"] = upload.audio_path.is_file()
        return _result(audio_path=upload.audio_path, audio_format="wav")

    monkeypatch.setattr(SpeechService, "render_live_to_file", render_live_to_file)
    monkeypatch.setattr(SpotifyService, "publish_rendered", publish_rendered)
    result = SpotifyService(app).publish_live(
        SpotifyLivePublishRequest(
            lines=lines,
            output=OutputRequest(),
            synthesis=SynthesisRequest(),
            title="Episode",
        )
    )

    temporary_path = captured["upload"].audio_path
    assert captured["lines"] is lines
    assert captured["output"].requested_format == "wav"
    assert captured["output"].force is True
    assert captured["temporary_exists"] is True
    assert not temporary_path.exists()
    assert result.audio_path is None
    assert result.render_summary is summary


def test_publish_live_preserves_explicit_audio_file(monkeypatch, tmp_path: Path) -> None:
    app = Readio(ReadioConfig())
    output_path = tmp_path / "episode.mp3"
    summary = RenderSummary(sample_rate=24000, sample_count=24000, channels=1)

    def render_live_to_file(self, _lines, output, **kwargs):
        assert output.requested_path == output_path
        output_path.write_bytes(b"audio")
        return RenderResult(
            plan=None,
            summary=summary,
            output_path=output_path,
            audio_format="mp3",
        )

    def publish_rendered(self, upload, _summary, *, chapters_from_markers, on_event=None):
        assert upload.audio_path.is_file()
        return _result(audio_path=upload.audio_path, audio_format="mp3")

    monkeypatch.setattr(SpeechService, "render_live_to_file", render_live_to_file)
    monkeypatch.setattr(SpotifyService, "publish_rendered", publish_rendered)
    result = SpotifyService(app).publish_live(
        SpotifyLivePublishRequest(
            lines=iter(("hello\n",)),
            output=OutputRequest(requested_format="mp3", requested_path=output_path),
            synthesis=SynthesisRequest(),
            title="Episode",
        )
    )

    assert output_path.read_bytes() == b"audio"
    assert result.audio_path == output_path
    assert result.audio_format == "mp3"


def test_live_cli_delegates_public_live_request_and_spotify_publish(monkeypatch, capsys) -> None:
    app = Readio(ReadioConfig())
    monkeypatch.setattr(spotify_cli, "Readio", lambda: app)
    monkeypatch.setattr(spotify_cli.sys, "stdin", io.StringIO("hello\n"))
    captured = {}

    class Service:
        def __init__(self, passed_app):
            assert passed_app is app

        def publish_live(self, request, *, on_event=None):
            captured["request"] = request
            captured["handler"] = on_event
            return _result()

    monkeypatch.setattr(spotify_cli, "SpotifyService", Service)
    args = cli.build_parser().parse_args(
        [
            "spotify",
            "publish",
            "--live",
            "--title",
            "Episode",
            "--format",
            "m4a",
            "--chapters-from-markers",
            "--json",
        ]
    )

    assert spotify_cli.cmd_spotify_publish(args) == 0
    request = captured["request"]
    assert request.lines is spotify_cli.sys.stdin
    assert request.output.requested_format == "m4a"
    assert request.output.requested_path is None
    assert request.title == "Episode"
    assert request.chapters_from_markers is True
    assert captured["handler"] is not None
    assert json.loads(capsys.readouterr().out)["audio_path"] is None


def test_spotify_parser_keeps_clean_command_family() -> None:
    parser = cli.build_parser()
    assert (
        parser.parse_args(["spotify", "publish", "text", "--title", "Episode"]).spotify_command
        == "publish"
    )
    assert (
        parser.parse_args(
            ["spotify", "upload", "episode.mp3", "--title", "Episode"]
        ).spotify_command
        == "upload"
    )
    assert parser.parse_args(["spotify", "shows"]).spotify_command == "shows"
    assert parser.parse_args(["spotify", "status", "abc"]).spotify_command == "status"
    assert parser.parse_args(["spotify", "doctor"]).spotify_command == "doctor"
