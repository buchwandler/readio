"""EPUB audiobook inspection and project creation through :mod:`readio.api`."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from .. import audiobook as audiobook_internal
from .. import errors as core_errors
from .. import jsonutil
from .. import project as project_internal
from . import errors as api_errors
from .events import EventHandler, ReadioEvent, compose_event_handlers
from .types import (
    AudiobookChapter,
    AudiobookInspection,
    AudiobookProjectChapter,
    AudiobookProjectResult,
    Diagnostic,
    ProjectRef,
)

if TYPE_CHECKING:
    from .app import Readio


class AudiobookService:
    """Inspect EPUB chapters and create ordinary chapter-scoped projects."""

    def __init__(self, app: Readio) -> None:
        self._app = app

    def inspect(
        self,
        source: Path,
        *,
        on_event: EventHandler | None = None,
    ) -> AudiobookInspection:
        handler = self._handler(on_event)
        operation = "audiobooks.inspect"
        self._notify(handler, ReadioEvent(kind="operation.started", operation=operation))
        source = self._resolve_source(source)
        if not source.is_file():
            raise api_errors.InputError(
                f"EPUB source is not a regular file: {source}",
                source_path=source,
                code="input.not_found",
            )
        inspection = self._call(lambda: audiobook_internal.inspect_epub(source))
        self._notify(handler, ReadioEvent(kind="operation.completed", operation=operation))
        return self._inspection(inspection)

    def create_project(
        self,
        source: Path,
        *,
        chapters: str = "all",
        output: Path | None = None,
        on_event: EventHandler | None = None,
    ) -> ProjectRef:
        return self.create_project_result(
            source, chapters=chapters, output=output, on_event=on_event
        ).project

    def create_project_result(
        self,
        source: Path,
        *,
        chapters: str = "all",
        output: Path | None = None,
        on_event: EventHandler | None = None,
    ) -> AudiobookProjectResult:
        handler = self._handler(on_event)
        operation = "audiobooks.create_project"
        self._notify(handler, ReadioEvent(kind="operation.started", operation=operation))
        source = self._resolve_source(source)
        if not source.is_file():
            raise api_errors.InputError(
                f"EPUB source is not a regular file: {source}",
                source_path=source,
                code="input.not_found",
            )
        project = self._call(
            lambda: audiobook_internal.init_audiobook_project(source, output, chapters)
        )
        project_ref = self._project_ref(project)
        selected = tuple(
            AudiobookProjectChapter(
                number=cast(int, scope.source_number),
                scope_id=scope.id,
                title=scope.title,
                level=scope.level or 1,
            )
            for scope in project.document_scopes()
        )
        self._notify(handler, ReadioEvent(kind="operation.completed", operation=operation))
        return AudiobookProjectResult(
            project=project_ref, source=source, chapters=selected
        )
    def _resolve_source(self, source: Path) -> Path:
        try:
            return source.expanduser().resolve()
        except Exception as error:
            raise api_errors.translate_exception(error, code="input.invalid_path") from error

    def _inspection(self, inspection: audiobook_internal.AudiobookInspection) -> AudiobookInspection:
        chapters = tuple(
            AudiobookChapter(
                number=chapter.number,
                source_id=chapter.source_id,
                title=chapter.title,
                href=chapter.href,
                parent_id=chapter.parent_id,
                level=chapter.level,
                char_count=chapter.char_count,
                markdown=chapter.markdown,
                diagnostics=tuple(self._diagnostic(row) for row in chapter.diagnostics),
            )
            for chapter in inspection.chapters
        )
        return AudiobookInspection(
            source=inspection.source,
            metadata=cast(dict[str, jsonutil.JsonValue], jsonutil.json_value(inspection.metadata)),
            chapters=chapters,
        )

    def _diagnostic(self, raw: object) -> Diagnostic:
        payload = jsonutil.json_value(raw)
        if not isinstance(payload, dict):
            payload = {"value": payload}
        code = str(payload.get("code") or payload.get("type") or "audiobook.diagnostic")
        severity = payload.get("severity")
        if severity not in {"info", "warning", "error"}:
            severity = "warning"
        message = str(payload.get("message") or payload.get("description") or code)
        return Diagnostic(
            code=code,
            severity=cast(str, severity),
            message=message,
            details=cast(dict[str, jsonutil.JsonValue], payload),
        )

    def _project_ref(self, project: project_internal.Project) -> ProjectRef:
        manifest = project.manifest
        return ProjectRef(
            root=project.root,
            project_id=manifest.project_id,
            name=manifest.name,
            kind=manifest.kind,
            source_format=manifest.source_format,
        )

    def _handler(self, on_event: EventHandler | None) -> EventHandler | None:
        return compose_event_handlers(self._app.on_event, on_event)

    def _notify(self, handler: EventHandler | None, event: ReadioEvent) -> None:
        if handler is None:
            return
        try:
            handler(event)
        except api_errors.ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.ExecutionError,
                code="event.handler_failed",
            ) from error

    def _call(self, callback):
        try:
            return callback()
        except core_errors.ReadioError:
            raise
        except FileNotFoundError as error:
            raise api_errors.InputError(
                str(error),
                source_path=Path(error.filename) if error.filename else None,
                code="input.not_found",
            ) from error
        except (TypeError, ValueError, KeyError) as error:
            message = str(error)
            if "already exists" in message:
                raise api_errors.ProjectConflictError(message, code="project.conflict") from error
            raise api_errors.translate_exception(error) from error
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.InvalidRequestError,
                code="audiobook.operation_failed",
            ) from error


__all__ = ["AudiobookService"]
