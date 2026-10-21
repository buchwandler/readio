"""UtterPlan public API adapter for SSMD bundle workspaces."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import utterplan  # type: ignore[import-not-found]

from readio.api.types import PlanRequest, RenderPlanRequest
from readio.workspace import WorkspaceRef

ProgressCallback = Callable[[Any], None] | None
StartCallback = Callable[[Any], None] | None


def compile_bundle(
    workspace: WorkspaceRef,
    request: PlanRequest,
    *,
    on_start: StartCallback = None,
    on_progress: ProgressCallback = None,
) -> Any:
    return utterplan.compile_bundle(
        workspace.root,
        chapters=request.chapters,
        language=request.language,
        planner_config=request.planner_config,
        diagnostics=request.diagnostics,
        force=request.force,
        on_start=on_start,
        on_progress=on_progress,
    )


def render_bundle_plan(
    workspace: WorkspaceRef,
    request: RenderPlanRequest,
    *,
    on_start: StartCallback = None,
    on_progress: ProgressCallback = None,
) -> Any:
    return utterplan.render_bundle(
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
