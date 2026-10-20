"""UtterPlan public API adapters for document and book workspaces."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import utterplan  # type: ignore[import-not-found]

from readio.api.types import PlanRequest, RenderPlanRequest
from readio.workspace import WorkspaceRef

ProgressCallback = Callable[[Any], None] | None
StartCallback = Callable[[Any], None] | None


def compile_document(
    workspace: WorkspaceRef,
    request: PlanRequest,
    *,
    on_start: StartCallback = None,
    on_progress: ProgressCallback = None,
) -> Any:
    return utterplan.compile_ssmdoc(
        workspace.root,
        language=request.language,
        planner_config=request.planner_config,
        diagnostics=request.diagnostics,
        force=request.force,
        on_start=on_start,
        on_progress=on_progress,
    )


def compile_book(
    workspace: WorkspaceRef,
    request: PlanRequest,
    *,
    on_start: StartCallback = None,
    on_progress: ProgressCallback = None,
) -> Any:
    return utterplan.compile_book(
        workspace.root,
        chapters=request.chapters,
        language=request.language,
        planner_config=request.planner_config,
        diagnostics=request.diagnostics,
        force=request.force,
        on_start=on_start,
        on_progress=on_progress,
    )


def render_document_plan(
    workspace: WorkspaceRef,
    request: RenderPlanRequest,
    *,
    on_start: StartCallback = None,
) -> Any:
    return utterplan.render_ssmdoc(
        workspace.root,
        profile=request.profile,
        variant=request.variant,
        utterplan_dir=request.utterplan_root,
        output_root=request.output_root,
        force=request.force,
        on_start=on_start,
    )


def render_book_plan(
    workspace: WorkspaceRef,
    request: RenderPlanRequest,
    *,
    on_start: StartCallback = None,
    on_progress: ProgressCallback = None,
) -> Any:
    return utterplan.render_book(
        workspace.root,
        profile=request.profile,
        variant=request.variant,
        chapters=request.chapters,
        utterplan_dir=request.utterplan_root,
        output_root=request.output_root,
        force=request.force,
        on_start=on_start,
        on_progress=on_progress,
    )
