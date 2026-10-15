"""Native .ssmdbook workflows exposed through :mod:`readio.api`."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from .. import book_pipeline, jsonutil
from ..integrations import ssmdconvert as ssmdconvert_internal
from . import errors as api_errors
from .events import EventHandler, ReadioEvent, compose_event_handlers
from .types import (
    SUPPORTED_AUDIOBOOK_FORMATS,
    AudiobookChapter,
    AudiobookExportOptions,
    AudiobookInspection,
    Diagnostic,
)

if TYPE_CHECKING:
    from .app import Readio


T = TypeVar("T")


class AudiobookService:
    """Inspect book sources and orchestrate stage-owned native book artifacts."""

    def __init__(self, app: Readio) -> None:
        self._app = app

    def inspect(
        self,
        source: Path,
        *,
        on_event: EventHandler | None = None,
    ) -> AudiobookInspection:
        source = self._resolve_source(source)
        return self._operation(
            "audiobooks.inspect",
            lambda: self._inspection(ssmdconvert_internal.inspect_book_source(source)),
            on_event=on_event,
        )

    def open_workspace(
        self,
        workspace: Path,
        *,
        on_event: EventHandler | None = None,
    ) -> book_pipeline.BookWorkspace:
        return self._operation(
            "audiobooks.open",
            lambda: book_pipeline.open_book_workspace(workspace),
            on_event=on_event,
        )

    def convert(
        self,
        source: Path,
        *,
        chapters: str = "all",
        language: str | None = None,
        output: Path | None = None,
        on_event: EventHandler | None = None,
    ) -> book_pipeline.BookWorkspace:
        return self._operation(
            "audiobooks.convert",
            lambda progress: book_pipeline.convert_book_source(
                source,
                output,
                chapters=chapters,
                language=language,
                on_progress=progress,
            ),
            on_event=on_event,
            progress=True,
        )

    def create_project(
        self,
        source: Path,
        *,
        chapters: str = "all",
        language: str | None = None,
        output: Path | None = None,
        settings: object | None = None,
        on_event: EventHandler | None = None,
    ) -> book_pipeline.BookWorkspace:
        """Compatibility name for native conversion; no Readio project is created."""
        if settings is not None:
            raise api_errors.InvalidRequestError(
                "native book workspaces use specialist profiles, not Readio project settings",
                code="audiobook.project_settings_unsupported",
            )
        return self.convert(
            source,
            chapters=chapters,
            language=language,
            output=output,
            on_event=on_event,
        )

    def create_project_result(
        self,
        source: Path,
        *,
        chapters: str = "all",
        language: str | None = None,
        output: Path | None = None,
        settings: object | None = None,
        on_event: EventHandler | None = None,
    ) -> book_pipeline.BookWorkspace:
        """Compatibility name returning the native workspace view directly."""
        return self.create_project(
            source,
            chapters=chapters,
            language=language,
            output=output,
            settings=settings,
            on_event=on_event,
        )

    def describe_project(
        self, workspace: Path | book_pipeline.BookWorkspace
    ) -> book_pipeline.BookWorkspace:
        """Compatibility name for opening a native workspace without attaching state."""
        path = workspace.root if isinstance(workspace, book_pipeline.BookWorkspace) else workspace
        return book_pipeline.open_book_workspace(path)

    def compile(
        self,
        workspace: Path | book_pipeline.BookWorkspace,
        *,
        chapters: str = "all",
        force: bool = False,
        fail_fast: bool = False,
        on_event: EventHandler | None = None,
    ) -> object:
        return self._operation(
            "audiobooks.compile",
            lambda progress: book_pipeline.compile_book(
                workspace,
                chapters=chapters,
                force=force,
                fail_fast=fail_fast,
                on_progress=progress,
            ),
            on_event=on_event,
            progress=True,
        )

    def render_plan(
        self,
        workspace: Path | book_pipeline.BookWorkspace,
        *,
        profile: Path,
        chapters: str = "all",
        variant: str | None = None,
        force: bool = False,
        fail_fast: bool = False,
        on_event: EventHandler | None = None,
    ) -> object:
        return self._operation(
            "audiobooks.render_plan",
            lambda progress: book_pipeline.render_book_plan(
                workspace,
                profile=profile,
                chapters=chapters,
                variant=variant,
                force=force,
                fail_fast=fail_fast,
                on_progress=progress,
            ),
            on_event=on_event,
            progress=True,
        )

    def synthesize(
        self,
        workspace: Path | book_pipeline.BookWorkspace,
        *,
        variant: str,
        chapters: str = "all",
        force: bool = False,
        fail_fast: bool = False,
        emphasis_policy: str = "defer-to-compose",
        on_event: EventHandler | None = None,
    ) -> object:
        return self._operation(
            "audiobooks.synthesize",
            lambda progress: book_pipeline.synthesize_book(
                workspace,
                variant=variant,
                chapters=chapters,
                force=force,
                fail_fast=fail_fast,
                emphasis_policy=emphasis_policy,
                on_progress=progress,
            ),
            on_event=on_event,
            progress=True,
        )

    def compose(
        self,
        workspace: Path | book_pipeline.BookWorkspace,
        *,
        variant: str,
        chapters: str = "all",
        force: bool = False,
        fail_fast: bool = False,
        on_event: EventHandler | None = None,
    ) -> object:
        return self._operation(
            "audiobooks.compose",
            lambda progress: book_pipeline.compose_book(
                workspace,
                variant=variant,
                chapters=chapters,
                force=force,
                fail_fast=fail_fast,
                on_progress=progress,
            ),
            on_event=on_event,
            progress=True,
        )

    def export(
        self,
        workspace: Path | book_pipeline.BookWorkspace,
        options: AudiobookExportOptions | None = None,
        *,
        variant: str,
        chapters: str = "all",
        on_event: EventHandler | None = None,
    ) -> object:
        options = options or AudiobookExportOptions()
        if options.format not in SUPPORTED_AUDIOBOOK_FORMATS:
            raise api_errors.InvalidRequestError(
                f"unsupported audiobook export format: {options.format}",
                code="audiobook.export.format_unsupported",
            )
        metadata = {
            key: value
            for key, value in (("title", options.title), ("author", options.author))
            if value is not None
        }
        return self._operation(
            "audiobooks.export",
            lambda progress: book_pipeline.export_book(
                workspace,
                variant=variant,
                chapters=chapters,
                force=options.force,
                output=options.output,
                profile=options.profile,
                metadata=metadata or None,
                cover=options.cover,
                bitrate=options.bitrate,
                on_progress=progress,
            ),
            on_event=on_event,
            progress=True,
        )

    def build(
        self,
        request: book_pipeline.BookBuildRequest,
        *,
        on_event: EventHandler | None = None,
    ) -> book_pipeline.BookBuildResult:
        return self._operation(
            "audiobooks.build",
            lambda progress: book_pipeline.build_book(request, on_progress=progress),
            on_event=on_event,
            progress=True,
        )

    def create_render_profile(self, path: Path, **options: Any) -> Path:
        return book_pipeline.create_render_profile(path, **options)

    def status(
        self,
        workspace: Path | book_pipeline.BookWorkspace,
        *,
        variant: str | None = None,
    ) -> dict[str, Any]:
        path = workspace.root if isinstance(workspace, book_pipeline.BookWorkspace) else workspace
        return book_pipeline.inspect_book_status(path, variant=variant)

    def _operation(
        self,
        operation: str,
        callback: Callable[..., T],
        *,
        on_event: EventHandler | None,
        progress: bool = False,
    ) -> T:
        handler = self._handler(on_event)
        self._notify(handler, ReadioEvent(kind="operation.started", operation=operation))
        result = callback(self._progress_handler(handler, operation)) if progress else callback()
        self._notify(handler, ReadioEvent(kind="operation.completed", operation=operation))
        return result

    def _progress_handler(
        self, handler: EventHandler | None, operation: str
    ) -> Callable[[book_pipeline.BookPipelineEvent], None] | None:
        if handler is None:
            return None

        def emit(event: book_pipeline.BookPipelineEvent) -> None:
            details = dict(event.details)
            details.update(
                {
                    "phase": event.phase,
                    "completed": event.completed,
                    "total": event.total,
                    "chapter_id": event.chapter_id,
                    "source_number": event.source_number,
                    "variant": event.variant,
                }
            )
            self._notify(
                handler,
                ReadioEvent(
                    kind=event.kind,
                    operation=operation,
                    stage=event.stage,
                    details=details,
                ),
            )

        return emit

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

    def _resolve_source(self, source: Path) -> Path:
        try:
            return source.expanduser().resolve()
        except Exception as error:
            raise api_errors.translate_exception(error, code="input.invalid_path") from error

    def _inspection(self, inspection: ssmdconvert_internal.BookInspection) -> AudiobookInspection:
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


__all__ = ["AudiobookService"]
