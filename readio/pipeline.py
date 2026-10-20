"""Workspace-kind dispatch for package-owned planning stages."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from readio.api.types import (
    BookExportRequest,
    ComposeRequest,
    DocumentExportRequest,
    PipelineEvent,
    PlanRequest,
    RenderPlanRequest,
    RenderPlanResult,
    VoiceRenderRequest,
)
from readio.errors import ReadioStageError
from readio.integrations.audiocompose import compose_book, compose_document
from readio.integrations.audioexport import export_book, export_document
from readio.integrations.utterplan import (
    compile_book,
    compile_document,
    render_book_plan,
    render_document_plan,
)
from readio.integrations.voicerender import render_book, render_document
from readio.progress import normalize_progress
from readio.workspace import WorkspaceRef

Callback = Callable[[PipelineEvent], None] | None


def plan(
    workspace: WorkspaceRef,
    request: PlanRequest,
    *,
    on_start: Callback = None,
    on_progress: Callback = None,
) -> Any:
    """Compile native document/book source through UtterPlan."""

    if workspace.kind == "document":
        _reject_document_chapters(request.chapters)
        operation = lambda: compile_document(
            workspace,
            request,
            on_start=normalize_progress("compile", on_start),
            on_progress=normalize_progress("compile", on_progress),
        )
    else:
        operation = lambda: compile_book(
            workspace,
            request,
            on_start=normalize_progress("compile", on_start),
            on_progress=normalize_progress("compile", on_progress),
        )
    return _call_stage("compile", workspace, operation)


def render_plan(
    workspace: WorkspaceRef,
    request: RenderPlanRequest,
    *,
    on_start: Callback = None,
    on_progress: Callback = None,
) -> RenderPlanResult:
    """Project a profile-specific RenderPlan and retain UtterPlan's exact variant."""

    if workspace.kind == "document":
        _reject_document_chapters(request.chapters)
        operation = lambda: render_document_plan(
            workspace,
            request,
            on_start=normalize_progress("render_plan", on_start),
        )
    else:
        operation = lambda: render_book_plan(
            workspace,
            request,
            on_start=normalize_progress("render_plan", on_start),
            on_progress=normalize_progress("render_plan", on_progress),
        )

    native_result = _call_stage("render_plan", workspace, operation)
    return RenderPlanResult(native=native_result, variant=native_result.variant)


def render(
    workspace: WorkspaceRef,
    request: VoiceRenderRequest,
    *,
    on_event: Callback = None,
) -> Any:
    """Render document/book speech through VoiceRender's workspace services."""

    if workspace.kind == "document":
        _reject_document_chapters(request.chapters)
        operation = lambda: render_document(
            workspace,
            request,
            on_event=normalize_progress("render", on_event),
        )
    else:
        operation = lambda: render_book(
            workspace,
            request,
            on_event=normalize_progress("render", on_event),
        )
    return _call_stage("render", workspace, operation)


def compose(
    workspace: WorkspaceRef,
    request: ComposeRequest,
    *,
    on_progress: Callback = None,
) -> Any:
    """Compose native rendered output through AudioCompose."""

    if workspace.kind == "document":
        _reject_document_chapters(request.chapters)
        operation = lambda: compose_document(
            workspace,
            request,
            on_progress=normalize_progress("compose", on_progress),
        )
    else:
        operation = lambda: compose_book(
            workspace,
            request,
            on_progress=normalize_progress("compose", on_progress),
        )
    return _call_stage("compose", workspace, operation)


def export(
    workspace: WorkspaceRef,
    request: DocumentExportRequest | BookExportRequest,
) -> Any:
    """Export composed output through AudioExport without rebuilding provenance paths."""

    if workspace.kind == "document":
        if not isinstance(request, DocumentExportRequest):
            raise TypeError("document workspaces require DocumentExportRequest")
        operation = lambda: export_document(workspace, request)
    else:
        if not isinstance(request, BookExportRequest):
            raise TypeError("book workspaces require BookExportRequest")
        operation = lambda: export_book(workspace, request)
    return _call_stage("export", workspace, operation)


def _reject_document_chapters(chapters: str) -> None:
    if chapters != "all":
        raise ValueError("chapter selectors are only valid for .ssmdbook workspaces")


def _call_stage(workspace_stage: str, workspace: WorkspaceRef, operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except ReadioStageError:
        raise
    except Exception as exc:
        code = getattr(exc, "code", None)
        error_code = code if isinstance(code, str) else None
        raise ReadioStageError(
            workspace_stage,
            str(exc),
            workspace=workspace.root,
            code=error_code,
            cause=exc,
        ) from exc
