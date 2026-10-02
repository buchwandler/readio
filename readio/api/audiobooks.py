"""Book-source inspection and project creation through :mod:`readio.api`."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeVar, cast

from .. import audiobook as audiobook_internal
from .. import errors as core_errors
from .. import jsonutil
from .. import project as project_internal
from ..integrations.ssmdconvert import (
    BookInputError,
    BookSelectionError,
    MissingInputDependencyError,
)
from ..project_model import ProjectFormatError as InternalProjectFormatError
from ..project_settings import project_settings_from_manifest
from ..stages import audiobook_export as audiobook_export_internal
from . import errors as api_errors
from .events import EventHandler, ReadioEvent, compose_event_handlers
from .types import (
    AUDIOBOOK_EXPORT_FORMAT,
    SUPPORTED_AUDIOBOOK_FORMATS,
    AudiobookChapter,
    AudiobookExportOptions,
    AudiobookExportResult,
    AudiobookInspection,
    AudiobookProjectChapter,
    AudiobookProjectDescription,
    AudiobookProjectResult,
    Diagnostic,
    ProjectLike,
    ProjectRef,
    ProjectSettings,
)

if TYPE_CHECKING:
    from .app import Readio


T = TypeVar("T")


def _audiobook_chapters(
    project: project_internal.Project,
) -> tuple[AudiobookProjectChapter, ...]:
    chapters = []
    for scope in project.document_scopes():
        if scope.source_number is None:
            raise InternalProjectFormatError(
                f"audiobook project scope {scope.id!r} has no source chapter number"
            )
        chapters.append(
            AudiobookProjectChapter(
                number=scope.source_number,
                scope_id=scope.id,
                title=scope.title or f"Chapter {scope.source_number}",
                level=scope.level or 1,
            )
        )
    return tuple(chapters)


def _export_options(
    internal: project_internal.Project,
    options: AudiobookExportOptions | None,
) -> AudiobookExportOptions:
    saved = project_settings_from_manifest(internal.manifest, internal.root).audiobook_export
    if options is None:
        return saved or AudiobookExportOptions()
    if saved is None:
        return options
    return replace(
        options,
        output=options.output if options.output is not None else saved.output,
        title=options.title if options.title is not None else saved.title,
        author=options.author if options.author is not None else saved.author,
        cover=options.cover if options.cover is not None else saved.cover,
        bitrate=options.bitrate if options.bitrate is not None else saved.bitrate,
    )


class AudiobookService:
    """Inspect book sources and create chapter-scoped audiobook projects."""

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
        inspection = self._call(
            lambda: audiobook_internal.inspect_book_source(source), source_path=source
        )
        self._notify(handler, ReadioEvent(kind="operation.completed", operation=operation))
        return self._inspection(inspection)

    def create_project(
        self,
        source: Path,
        *,
        chapters: str = "all",
        output: Path | None = None,
        settings: ProjectSettings | None = None,
        on_event: EventHandler | None = None,
    ) -> ProjectRef:
        return self.create_project_result(
            source, chapters=chapters, output=output, settings=settings, on_event=on_event
        ).project

    def create_project_result(
        self,
        source: Path,
        *,
        chapters: str = "all",
        output: Path | None = None,
        settings: ProjectSettings | None = None,
        on_event: EventHandler | None = None,
    ) -> AudiobookProjectResult:
        handler = self._handler(on_event)
        operation = "audiobooks.create_project"
        self._notify(handler, ReadioEvent(kind="operation.started", operation=operation))
        source = self._resolve_source(source)
        project = self._call(
            lambda: audiobook_internal.init_audiobook_project(source, output, chapters),
            source_path=source,
        )
        project_ref = self._project_ref(project)
        if settings is not None:
            self._app.projects.configure(project_ref, settings)
        selected = _audiobook_chapters(project)
        self._notify(handler, ReadioEvent(kind="operation.completed", operation=operation))
        return AudiobookProjectResult(project=project_ref, source=source, chapters=selected)

    def describe_project(self, project: ProjectLike) -> AudiobookProjectDescription:
        """Describe the persisted chapter scope of an existing audiobook project."""
        project_path = project.root if isinstance(project, ProjectRef) else project
        internal = self._call(lambda: project_internal.load_project(project_path))
        if internal.manifest.kind != "audiobook":
            raise api_errors.InvalidRequestError(
                "project is not an audiobook project",
                code="audiobook.project_kind_invalid",
            )
        return AudiobookProjectDescription(
            project=self._project_ref(internal),
            source=internal.path(internal.manifest.source_path),
            chapters=self._call(lambda: _audiobook_chapters(internal)),
        )

    def export(
        self,
        project: ProjectLike,
        options: AudiobookExportOptions | None = None,
        *,
        on_event: EventHandler | None = None,
    ) -> AudiobookExportResult:
        if options is not None and options.format not in SUPPORTED_AUDIOBOOK_FORMATS:
            raise api_errors.InvalidRequestError(
                f"unsupported audiobook export format: {options.format}",
                code="audiobook.export.format_unsupported",
            )
        handler = self._handler(on_event)
        operation = "audiobooks.export"
        self._notify(handler, ReadioEvent(kind="operation.started", operation=operation))
        project_path = project.root if isinstance(project, ProjectRef) else project
        internal = self._call(lambda: project_internal.load_project(project_path))
        options = _export_options(internal, options)
        self._notify(
            handler, ReadioEvent(kind="stage.started", operation=operation, stage="export")
        )
        raw = self._call(
            lambda: audiobook_export_internal.export_audiobook_project(
                internal,
                output=options.output,
                title=options.title,
                author=options.author,
                cover=options.cover,
                bitrate=options.bitrate,
                force=options.force,
            )
        )
        self._notify(
            handler,
            ReadioEvent(
                kind="stage.completed",
                operation=operation,
                stage="export",
                details={
                    "format": AUDIOBOOK_EXPORT_FORMAT,
                    "chapter_count": int(raw["chapter_count"]),
                },
            ),
        )
        self._notify(handler, ReadioEvent(kind="operation.completed", operation=operation))
        return AudiobookExportResult(
            project=self._project_ref(internal),
            output_path=Path(raw["path"]),
            format=AUDIOBOOK_EXPORT_FORMAT,
            output_sha256=str(raw["output_sha256"]),
            export_id=str(raw["export_id"]),
            chapter_count=int(raw["chapter_count"]),
        )

    def build(
        self,
        project: ProjectLike,
        options: AudiobookExportOptions | None = None,
        *,
        on_event: EventHandler | None = None,
    ) -> AudiobookExportResult:
        """Plan, synthesize, compose, and export an audiobook project."""
        self.describe_project(project)
        self._app.projects.plan(project, on_event=on_event)
        self._app.projects.synthesize(project, on_event=on_event)
        self._app.projects.compose(project, on_event=on_event)
        return self.export(project, options, on_event=on_event)

    def _resolve_source(self, source: Path) -> Path:
        try:
            return source.expanduser().resolve()
        except Exception as error:
            raise api_errors.translate_exception(error, code="input.invalid_path") from error

    def _inspection(
        self, inspection: audiobook_internal.AudiobookInspection
    ) -> AudiobookInspection:
        chapters = tuple(
            AudiobookChapter(
                number=chapter.number,
                source_id=chapter.source_id,
                title=chapter.title,
                href=chapter.href,
                source_parent_id=chapter.source_parent_id,
                parent_id=chapter.parent_id,
                level=chapter.level,
                char_count=chapter.char_count,
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
            severity=cast(Literal["info", "warning", "error"], severity),
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

    def _call(self, callback: Callable[[], T], *, source_path: Path | None = None) -> T:
        try:
            return callback()
        except BookSelectionError as error:
            raise api_errors.InvalidRequestError(
                str(error),
                source_path=error.source_path or source_path,
                code="request.chapter_selection_invalid",
            ) from error
        except MissingInputDependencyError as error:
            raise api_errors.IntegrationError(
                str(error),
                source_path=error.source_path or source_path,
                details=error.details,
                code=error.code,
            ) from error
        except BookInputError as error:
            raise api_errors.InputError(
                str(error),
                source_path=error.source_path or source_path,
                details=error.details,
                code=error.code,
            ) from error
        except core_errors.ReadioError:
            raise
        except audiobook_export_internal.AudiobookExportError as error:
            if error.code == "audiobook.export.output_exists":
                raise api_errors.OutputError(
                    str(error), details=error.details, code=error.code
                ) from error
            raise api_errors.ExecutionError(
                str(error), details=error.details, code=error.code
            ) from error
        except FileNotFoundError as error:
            raise api_errors.InputError(
                str(error),
                source_path=source_path or (Path(error.filename) if error.filename else None),
                code="input.not_found",
            ) from error
        except InternalProjectFormatError as error:
            raise api_errors.ProjectFormatError(str(error), code="project.invalid") from error
        except project_internal.ProjectError as error:
            message = str(error)
            if "locked" in message.lower():
                raise api_errors.ProjectConflictError(message, code="project.locked") from error
            if "not a Readio project" in message:
                raise api_errors.ProjectNotFoundError(message, code="project.not_found") from error
            if "already exists" in message:
                raise api_errors.ProjectConflictError(message, code="project.conflict") from error
            raise api_errors.ProjectError(message, code="project.invalid") from error
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
