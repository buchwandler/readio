"""Kind-neutral orchestration of package-owned bundle pipeline stages."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from readio.api.types import (
    ComposeRequest,
    ExportRequest,
    PipelineEvent,
    PlanRequest,
    RenderPlanRequest,
    RenderPlanResult,
    VoiceRenderRequest,
)
from readio.errors import ReadioStageError
from readio.integrations.audiocompose import compose_bundle
from readio.integrations.audioexport import export_bundle
from readio.integrations.utterplan import compile_bundle, render_bundle_plan
from readio.integrations.voicerender import render_bundle
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
    """Compile bundle content through UtterPlan."""

    operation = lambda: compile_bundle(
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

    operation = lambda: render_bundle_plan(
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
    """Render speech through VoiceRender's bundle service."""

    operation = lambda: render_bundle(
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
    """Compose rendered output through AudioCompose's bundle service."""

    operation = lambda: compose_bundle(
        workspace,
        request,
        on_progress=normalize_progress("compose", on_progress),
    )
    return _call_stage("compose", workspace, operation)


def export(workspace: WorkspaceRef, request: ExportRequest) -> Any:
    """Export composed output through AudioExport without rebuilding provenance paths."""

    return _call_stage("export", workspace, lambda: export_bundle(workspace, request))


def _call_stage(stage: str, workspace: WorkspaceRef, operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except ReadioStageError:
        raise
    except Exception as exc:
        code = getattr(exc, "code", None)
        error_code = code if isinstance(code, str) else None
        raise ReadioStageError(
            stage,
            str(exc),
            workspace=workspace.root,
            code=error_code,
            cause=exc,
        ) from exc
