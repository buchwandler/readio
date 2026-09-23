"""Public synchronous integration with the optional save-to-spotify CLI."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from ... import spotify as _spotify
from ...audio import RenderSummary
from ...errors import ReadioError
from ...formats import AudioFormat, audio_format_from_suffix, format_suffix, resolve_audio_format
from ...jsonutil import JsonValue, json_value
from ..app import Readio
from ..errors import (
    InputError,
    IntegrationError,
    InvalidRequestError,
    OutputError,
    translate_exception,
)
from ..events import EventHandler, ReadioEvent, compose_event_handlers
from ..types import PlanRequest

SpotifyTimeline = Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class SpotifyPublishRequest:
    render: PlanRequest
    title: str
    show_id: str | None = None
    new_show: str | None = None
    summary: str | None = None
    image: Path | None = None
    language: str | None = None
    timeline: Path | None = None
    chapters_from_markers: bool = False
    wait: bool = False
    wait_timeout: str | None = None
    api_timeout: str | None = None


@dataclass(frozen=True, slots=True)
class SpotifyUploadRequest:
    audio_path: Path
    title: str
    show_id: str | None = None
    new_show: str | None = None
    summary: str | None = None
    image: Path | None = None
    language: str | None = None
    timeline: Path | SpotifyTimeline | None = None
    wait: bool = False
    wait_timeout: str | None = None
    api_timeout: str | None = None


@dataclass(frozen=True, slots=True)
class SpotifyDoctorResult:
    ok: bool
    details: Mapping[str, JsonValue]

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class SpotifyShow:
    show_uri: str
    title: str
    language: str | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class SpotifyReadinessResult:
    episode_uri: str
    readiness: str

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class SpotifyPublishResult:
    episode_uri: str
    upload_status: str | None
    readiness: SpotifyReadinessResult | None
    audio_path: Path | None
    audio_format: AudioFormat
    timeline_published: bool
    render_summary: RenderSummary | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


class SpotifyService:
    """Publish rendered or existing audio through save-to-spotify."""

    def __init__(self, app: Readio) -> None:
        self._app = app

    def doctor(self, *, api_timeout: str | None = None) -> SpotifyDoctorResult:
        try:
            payload = _spotify.doctor(api_timeout=api_timeout)
        except ReadioError:
            raise
        except Exception as error:
            raise self._integration_error(error, "spotify.doctor_failed") from error
        serializable = json_value(payload)
        if not isinstance(serializable, dict):
            raise IntegrationError(
                "save-to-spotify doctor returned an invalid response",
                code="spotify.invalid_response",
            )
        return SpotifyDoctorResult(ok=True, details=serializable)

    def shows(self, *, api_timeout: str | None = None) -> tuple[SpotifyShow, ...]:
        try:
            values = _spotify.list_shows(api_timeout=api_timeout)
        except ReadioError:
            raise
        except Exception as error:
            raise self._integration_error(error, "spotify.shows_failed") from error
        return tuple(SpotifyShow(item.show_uri, item.title, item.language) for item in values)

    def upload(
        self,
        request: SpotifyUploadRequest,
        *,
        on_event: EventHandler | None = None,
    ) -> SpotifyPublishResult:
        self._validate_metadata(request.title, request.show_id, request.new_show)
        audio_path = request.audio_path.expanduser()
        if not audio_path.is_file():
            raise InputError(
                f"audio file does not exist or is not a regular file: {audio_path}",
                source_path=audio_path,
                code="spotify.audio_not_found",
            )
        audio_format = audio_format_from_suffix(audio_path)
        if audio_format is None:
            raise InvalidRequestError(
                "Spotify upload supports .wav, .mp3, .m4a, and .ogg files",
                source_path=audio_path,
                code="spotify.audio_format_unsupported",
            )
        if request.timeline is not None:
            self._validate_timeline(request.timeline)

        self._emit(on_event, "operation.started", "spotify.upload")
        try:
            uploaded = _spotify.upload_episode(
                audio_path,
                title=request.title,
                show_id=request.show_id,
                new_show=request.new_show,
                summary=request.summary,
                image=request.image,
                language=request.language,
                api_timeout=request.api_timeout,
            )
        except ReadioError:
            raise
        except Exception as error:
            raise self._integration_error(error, "spotify.upload_failed") from error

        readiness = None
        timeline_published = False
        if request.wait or request.timeline is not None:
            self._emit(
                on_event,
                "stage.started",
                "spotify.upload",
                stage="readiness",
                message="Waiting for Spotify readiness",
            )
            readiness = self.status(
                uploaded.episode_uri,
                wait=True,
                wait_timeout=request.wait_timeout,
                api_timeout=request.api_timeout,
                on_event=on_event,
            )
        if request.timeline is not None:
            if readiness is None or readiness.readiness != "READY":
                raise IntegrationError(
                    "episode is not READY; cannot set timeline",
                    code="spotify.episode_not_ready",
                )
            self.set_timeline(
                uploaded.episode_uri,
                request.timeline,
                api_timeout=request.api_timeout,
            )
            timeline_published = True

        result = SpotifyPublishResult(
            episode_uri=uploaded.episode_uri,
            upload_status=uploaded.status,
            readiness=readiness,
            audio_path=audio_path,
            audio_format=audio_format,
            timeline_published=timeline_published,
        )
        self._emit(on_event, "operation.completed", "spotify.upload")
        return result

    def publish(
        self,
        request: SpotifyPublishRequest,
        *,
        on_event: EventHandler | None = None,
    ) -> SpotifyPublishResult:
        self._validate_metadata(request.title, request.show_id, request.new_show)
        if request.render.operation != "render" or request.render.output.mode != "file":
            raise InvalidRequestError(
                "Spotify publishing requires a bounded render request with file output",
                code="spotify.render_request_invalid",
            )
        if request.chapters_from_markers and request.timeline is not None:
            raise InvalidRequestError(
                "provide either a timeline file or chapters from markers, not both",
                code="spotify.timeline_conflict",
            )
        if request.timeline is not None:
            self._validate_timeline(request.timeline)

        requested_path = request.render.output.requested_path
        if requested_path is not None:
            requested_path = requested_path.expanduser()
            try:
                requested_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise translate_exception(
                    error,
                    error_type=OutputError,
                    code="spotify.output_directory_failed",
                ) from error
        try:
            audio_format = resolve_audio_format(
                requested=request.render.output.requested_format,
                output=requested_path,
            )
        except Exception as error:
            raise translate_exception(
                error,
                error_type=InvalidRequestError,
                code="spotify.audio_format_invalid",
            ) from error

        temporary_path = None
        output_path = requested_path
        if output_path is None:
            try:
                descriptor, name = tempfile.mkstemp(suffix=format_suffix(audio_format))
                os.close(descriptor)
            except OSError as error:
                raise translate_exception(
                    error,
                    error_type=OutputError,
                    code="spotify.temporary_output_failed",
                ) from error
            temporary_path = Path(name)
            output_path = temporary_path

        render_request = replace(
            request.render,
            output=replace(
                request.render.output,
                requested_format=audio_format,
                requested_path=output_path,
                force=request.render.output.force or temporary_path is not None,
            ),
        )
        self._emit(on_event, "operation.started", "spotify.publish")
        self._emit(
            on_event,
            "stage.started",
            "spotify.publish",
            stage="render",
            message="Rendering episode audio",
        )
        try:
            rendered = self._app.speech.render(render_request, on_event=on_event)
            self._emit(
                on_event,
                "stage.started",
                "spotify.publish",
                stage="upload",
                message="Uploading episode",
            )
            uploaded = self.publish_rendered(
                SpotifyUploadRequest(
                    audio_path=rendered.output_path or output_path,
                    title=request.title,
                    show_id=request.show_id,
                    new_show=request.new_show,
                    summary=request.summary,
                    image=request.image,
                    language=request.language,
                    timeline=request.timeline,
                    wait=request.wait,
                    wait_timeout=request.wait_timeout,
                    api_timeout=request.api_timeout,
                ),
                rendered.summary,
                chapters_from_markers=request.chapters_from_markers,
                on_event=on_event,
            )
            result = replace(
                uploaded,
                audio_path=rendered.output_path if temporary_path is None else None,
                audio_format=audio_format,
            )
            self._emit(on_event, "operation.completed", "spotify.publish")
            return result
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError as error:
                    raise translate_exception(
                        error,
                        error_type=OutputError,
                        code="spotify.temporary_output_cleanup_failed",
                    ) from error

    def publish_rendered(
        self,
        request: SpotifyUploadRequest,
        summary: RenderSummary,
        *,
        chapters_from_markers: bool = False,
        on_event: EventHandler | None = None,
    ) -> SpotifyPublishResult:
        if chapters_from_markers and request.timeline is not None:
            raise InvalidRequestError(
                "provide either a timeline or chapters from markers, not both",
                code="spotify.timeline_conflict",
            )
        timeline = request.timeline
        if chapters_from_markers:
            try:
                timeline = cast(
                    SpotifyTimeline,
                    _spotify.build_timeline(summary.markers, summary.sample_rate),
                )
            except Exception as error:
                raise self._integration_error(error, "spotify.timeline_invalid") from error
        result = self.upload(replace(request, timeline=timeline), on_event=on_event)
        return replace(result, render_summary=summary)

    def status(
        self,
        episode: str,
        *,
        wait: bool = False,
        wait_timeout: str | None = None,
        api_timeout: str | None = None,
        on_event: EventHandler | None = None,
    ) -> SpotifyReadinessResult:
        self._emit(on_event, "operation.started", "spotify.status")
        try:
            result = _spotify.episode_status(
                episode,
                wait=wait,
                wait_timeout=wait_timeout,
                api_timeout=api_timeout,
            )
        except ReadioError:
            raise
        except Exception as error:
            raise self._integration_error(error, "spotify.status_failed") from error
        value = SpotifyReadinessResult(result.episode_uri, result.readiness)
        self._emit(on_event, "operation.completed", "spotify.status")
        return value

    def set_timeline(
        self,
        episode: str,
        timeline: SpotifyTimeline | Path,
        *,
        api_timeout: str | None = None,
    ) -> None:
        path, temporary_path = self._timeline_path(timeline)
        try:
            _spotify.set_timeline(episode, path, api_timeout=api_timeout)
        except ReadioError:
            raise
        except Exception as error:
            raise self._integration_error(error, "spotify.timeline_failed") from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError as error:
                    raise translate_exception(
                        error,
                        error_type=OutputError,
                        code="spotify.timeline_temporary_cleanup_failed",
                    ) from error

    def _validate_timeline(self, timeline: SpotifyTimeline | Path) -> None:
        if isinstance(timeline, Path):
            path = timeline.expanduser()
            if not path.is_file():
                raise InputError(
                    f"timeline file does not exist or is not a regular file: {path}",
                    source_path=path,
                    code="spotify.timeline_not_found",
                )
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise InputError(
                    f"timeline file is not valid JSON: {path}",
                    source_path=path,
                    details={"exception_type": type(error).__name__},
                    code="spotify.timeline_invalid",
                ) from error
        else:
            payload = json_value(timeline)
        if not isinstance(payload, dict):
            raise InvalidRequestError(
                "timeline JSON must contain an object",
                code="spotify.timeline_invalid",
            )

    def _timeline_path(
        self,
        timeline: SpotifyTimeline | Path,
    ) -> tuple[Path, Path | None]:
        if isinstance(timeline, Path):
            path = timeline.expanduser()
            self._validate_timeline(path)
            return path, None
        self._validate_timeline(timeline)
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".json",
                prefix="readio-timeline-",
                delete=False,
            ) as timeline_file:
                json.dump(json_value(timeline), timeline_file, ensure_ascii=False)
                path = Path(timeline_file.name)
        except OSError as error:
            raise translate_exception(
                error,
                error_type=OutputError,
                code="spotify.timeline_temporary_file_failed",
            ) from error
        return path, path

    @staticmethod
    def _validate_metadata(title: str, show_id: str | None, new_show: str | None) -> None:
        if not title.strip():
            raise InvalidRequestError("episode title must not be empty", code="spotify.title_empty")
        if show_id is not None and new_show is not None:
            raise InvalidRequestError(
                "show_id and new_show are mutually exclusive",
                code="spotify.show_selection_conflict",
            )

    def _emit(
        self,
        on_event: EventHandler | None,
        kind: str,
        operation: str,
        *,
        stage: str | None = None,
        message: str | None = None,
    ) -> None:
        handler = compose_event_handlers(self._app.on_event, on_event)
        if handler is not None:
            handler(
                ReadioEvent(
                    kind=kind,
                    operation=operation,
                    stage=stage,
                    message=message,
                )
            )

    @staticmethod
    def _integration_error(error: Exception, code: str) -> IntegrationError:
        if isinstance(error, IntegrationError):
            return error
        if isinstance(error, _spotify.SpotifyError):
            return IntegrationError(
                str(error),
                details={"exception_type": type(error).__name__},
                code=code,
            )
        translated = translate_exception(error, error_type=IntegrationError, code=code)
        return cast(IntegrationError, translated)


__all__ = [
    "SpotifyDoctorResult",
    "SpotifyPublishRequest",
    "SpotifyPublishResult",
    "SpotifyReadinessResult",
    "SpotifyService",
    "SpotifyShow",
    "SpotifyTimeline",
    "SpotifyUploadRequest",
]
