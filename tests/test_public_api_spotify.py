from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from readio.api import (
    Document,
    InputRequest,
    IntegrationError,
    OutputRequest,
    PlanRequest,
    Readio,
    RenderResult,
    RenderSummary,
    SynthesisRequest,
)
from readio.api.integrations import spotify as spotify_api
from readio.spotify import SpotifyCommandError, SpotifyReadinessResult, SpotifyUploadResult
from readio.spotify import SpotifyShow as InternalSpotifyShow


def _request(output: Path | None = None) -> PlanRequest:
    return PlanRequest(
        operation="render",
        input=InputRequest(
            document=Document(text="Episode audio", source_path=None, format="text"),
            source_kind="literal",
        ),
        output=OutputRequest(requested_format="wav", requested_path=output),
    )


def _fake_render(app: Readio, monkeypatch, summary: RenderSummary):
    rendered_paths: list[Path] = []

    def render(request: PlanRequest, *, on_event=None, write_manifest=False) -> RenderResult:
        assert not write_manifest
        assert request.output.requested_path is not None
        path = request.output.requested_path
        path.write_bytes(b"rendered audio")
        rendered_paths.append(path)
        return RenderResult(plan=None, summary=summary, output_path=path)

    monkeypatch.setattr(app.speech, "render", render)
    return rendered_paths


def test_publish_renders_through_speech_service_and_cleans_temporary_audio(monkeypatch) -> None:
    app = Readio()
    summary = RenderSummary(sample_rate=24000, sample_count=24000, channels=1)
    rendered_paths = _fake_render(app, monkeypatch, summary)
    uploaded_paths: list[tuple[Path, bytes]] = []

    def upload(path: Path, **kwargs):
        uploaded_paths.append((path, path.read_bytes()))
        return SpotifyUploadResult("spotify:episode:1", "UPLOADING")

    monkeypatch.setattr(spotify_api._spotify, "upload_episode", upload)

    result = spotify_api.SpotifyService(app).publish(
        spotify_api.SpotifyPublishRequest(render=_request(), title="Episode")
    )

    assert result.episode_uri == "spotify:episode:1"
    assert result.audio_format == "wav"
    assert result.audio_path is None
    assert result.render_summary is summary
    assert len(uploaded_paths) == 1
    uploaded_path, uploaded_bytes = uploaded_paths[0]
    assert uploaded_path == rendered_paths[0]
    assert uploaded_bytes == b"rendered audio"
    assert not uploaded_path.exists()
    assert json.loads(json.dumps(result.to_dict()))["audio_path"] is None


def test_publish_live_renders_and_uploads_through_public_services(monkeypatch) -> None:
    app = Readio()
    lines = iter(("Episode audio\n",))
    summary = RenderSummary(
        sample_rate=24000,
        sample_count=24000,
        channels=1,
        markers=({"name": "intro", "sample_offset": 0},),
    )
    render_calls = []
    uploaded: list[tuple[Path, bytes]] = []

    def render_live(passed_lines, output, **kwargs):
        assert passed_lines is lines
        assert tuple(passed_lines) == ("Episode audio\n",)
        assert output.requested_path is not None
        output.requested_path.write_bytes(b"rendered live audio")
        render_calls.append(output)
        return RenderResult(
            plan=None,
            summary=summary,
            output_path=output.requested_path,
            audio_format="wav",
        )

    def upload(path: Path, **kwargs):
        uploaded.append((path, path.read_bytes()))
        return SpotifyUploadResult("spotify:episode:live", "UPLOADING")

    monkeypatch.setattr(app.speech, "render_live_to_file", render_live)
    monkeypatch.setattr(spotify_api._spotify, "upload_episode", upload)

    result = spotify_api.SpotifyService(app).publish_live(
        spotify_api.SpotifyLivePublishRequest(
            lines=lines,
            output=OutputRequest(requested_format="wav"),
            synthesis=SynthesisRequest(),
            title="Live episode",
        )
    )

    temporary_path = uploaded[0][0]
    assert render_calls[0].force is True
    assert uploaded == [(temporary_path, b"rendered live audio")]
    assert not temporary_path.exists()
    assert result.episode_uri == "spotify:episode:live"
    assert result.audio_path is None
    assert result.render_summary is summary


def test_publish_retains_requested_output_waits_and_sets_marker_timeline(
    monkeypatch, tmp_path: Path
) -> None:
    app = Readio()
    output = tmp_path / "nested" / "episode.wav"
    summary = RenderSummary(
        sample_rate=24000,
        sample_count=48000,
        channels=1,
        markers=(
            {"name": "intro", "sample_offset": 0},
            {"name": "topic", "sample_offset": 24000},
        ),
    )
    _fake_render(app, monkeypatch, summary)
    calls: list[tuple[str, object]] = []

    def upload(path: Path, **kwargs):
        assert path.read_bytes() == b"rendered audio"
        return SpotifyUploadResult("spotify:episode:2", "PROCESSING")

    def status(episode: str, **kwargs):
        calls.append(("status", kwargs))
        return SpotifyReadinessResult(episode, "READY")

    def set_timeline(episode: str, path: Path, **kwargs):
        payload = json.loads(path.read_text(encoding="utf-8"))
        calls.append(("timeline", (episode, payload, path.exists())))

    monkeypatch.setattr(spotify_api._spotify, "upload_episode", upload)
    monkeypatch.setattr(spotify_api._spotify, "episode_status", status)
    monkeypatch.setattr(spotify_api._spotify, "set_timeline", set_timeline)

    result = spotify_api.SpotifyService(app).publish(
        spotify_api.SpotifyPublishRequest(
            render=_request(output),
            title="Episode",
            chapters_from_markers=True,
            wait=True,
            wait_timeout="2m",
        )
    )

    assert output.read_bytes() == b"rendered audio"
    assert result.audio_path == output
    assert result.readiness is not None and result.readiness.readiness == "READY"
    assert result.timeline_published
    assert calls[0] == ("status", {"wait": True, "wait_timeout": "2m", "api_timeout": None})
    timeline_episode, timeline, file_existed = calls[1][1]
    assert timeline_episode == "spotify:episode:2"
    assert timeline["items"][1]["chapter"]["start_time_ms"] == 1000
    assert file_existed


def test_upload_preserves_caller_audio_and_timeline_files(monkeypatch, tmp_path: Path) -> None:
    app = Readio()
    audio = tmp_path / "recording.mp3"
    audio.write_bytes(b"caller audio")
    timeline = tmp_path / "timeline.json"
    original = '{"items": [{"chapter": {"title": "One"}}]}'
    timeline.write_text(original, encoding="utf-8")
    observed: list[Path] = []
    monkeypatch.setattr(
        spotify_api._spotify,
        "upload_episode",
        lambda path, **kwargs: SpotifyUploadResult("spotify:episode:3", "PROCESSING"),
    )
    monkeypatch.setattr(
        spotify_api._spotify,
        "episode_status",
        lambda episode, **kwargs: SpotifyReadinessResult(episode, "READY"),
    )
    monkeypatch.setattr(
        spotify_api._spotify,
        "set_timeline",
        lambda episode, path, **kwargs: observed.append(path),
    )

    result = spotify_api.SpotifyService(app).upload(
        spotify_api.SpotifyUploadRequest(
            audio_path=audio,
            title="Episode",
            timeline=timeline,
            wait_timeout="30s",
        )
    )

    assert result.audio_path == audio
    assert result.timeline_published
    assert observed == [timeline]
    assert audio.read_bytes() == b"caller audio"
    assert timeline.read_text(encoding="utf-8") == original


def test_spotify_provider_errors_are_translated(monkeypatch, tmp_path: Path) -> None:
    audio = tmp_path / "recording.wav"
    audio.write_bytes(b"wav")
    monkeypatch.setattr(
        spotify_api._spotify,
        "upload_episode",
        lambda *args, **kwargs: (_ for _ in ()).throw(SpotifyCommandError("upstream failed")),
    )

    with pytest.raises(IntegrationError) as error:
        spotify_api.SpotifyService(Readio()).upload(
            spotify_api.SpotifyUploadRequest(audio_path=audio, title="Episode")
        )

    assert error.value.code == "spotify.upload_failed"


def test_public_spotify_event_handlers_receive_typed_events(monkeypatch, tmp_path: Path) -> None:
    app_events = []
    operation_events = []
    app = Readio(on_event=app_events.append)
    audio = tmp_path / "recording.wav"
    audio.write_bytes(b"wav")
    monkeypatch.setattr(
        spotify_api._spotify,
        "upload_episode",
        lambda path, **kwargs: SpotifyUploadResult("spotify:episode:4", "UPLOADING"),
    )

    spotify_api.SpotifyService(app).upload(
        spotify_api.SpotifyUploadRequest(audio_path=audio, title="Episode"),
        on_event=operation_events.append,
    )

    assert operation_events[0].operation == "spotify.upload"
    assert operation_events[0].kind == "operation.started"
    assert app_events[0] == operation_events[0]
    assert operation_events[-1].kind == "operation.completed"


def test_doctor_shows_and_status_return_public_types(monkeypatch) -> None:
    monkeypatch.setattr(
        spotify_api._spotify, "doctor", lambda **kwargs: {"ok": True, "user": "member"}
    )
    monkeypatch.setattr(
        spotify_api._spotify,
        "list_shows",
        lambda **kwargs: (InternalSpotifyShow("spotify:show:1", "Show", "en"),),
    )
    monkeypatch.setattr(
        spotify_api._spotify,
        "episode_status",
        lambda episode, **kwargs: SpotifyReadinessResult(episode, "READY"),
    )
    service = spotify_api.SpotifyService(Readio())

    doctor = service.doctor(api_timeout="5s")
    shows = service.shows(api_timeout="5s")
    status = service.status("spotify:episode:5", api_timeout="5s")

    assert doctor.ok and doctor.details["user"] == "member"
    assert json.loads(json.dumps(doctor.to_dict()))["details"]["ok"]
    assert shows == (spotify_api.SpotifyShow("spotify:show:1", "Show", "en"),)
    assert status.to_dict() == {"episode_uri": "spotify:episode:5", "readiness": "READY"}


def test_importing_core_api_does_not_import_spotify_integration() -> None:
    code = (
        "import sys; import readio.api; "
        "assert 'readio.spotify' not in sys.modules; "
        "assert 'readio.api.integrations.spotify' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
