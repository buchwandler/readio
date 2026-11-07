"""UtterPlan public API adapter for SSMD bundle workspaces."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

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


def inspect_bundle_compile(
    workspace: WorkspaceRef,
    *,
    chapters: str = "all",
    language: str | None = None,
    planner_config: Any | None = None,
    output_dir: str | Path | None = None,
) -> Any:
    """Inspect selected compilation state through UtterPlan's public API."""
    return utterplan.inspect_bundle_compile(
        workspace.root,
        chapters=chapters,
        language=language,
        planner_config=planner_config,
        output_dir=output_dir,
    )


def inspect_bundle_projection(
    workspace: WorkspaceRef,
    *,
    profile: str | Path,
    chapters: str = "all",
    variant: str | None = None,
    pause_timing: Any | None = None,
    utterplan_dir: str | Path | None = None,
    output_root: str | Path | None = None,
) -> Any:
    """Inspect selected RenderPlan projection state without materializing it."""
    return utterplan.inspect_bundle_projection(
        workspace.root,
        profile=profile,
        chapters=chapters,
        variant=variant,
        pause_timing=pause_timing,
        utterplan_dir=utterplan_dir,
        output_root=output_root,
    )


def planner_config(language: str) -> Any:
    """Build UtterPlan's public configuration for literal speech compilation."""
    return utterplan.PlannerConfig(language=language)


def compile_document(
    text: str,
    *,
    input_format: Literal["ssmd", "plain"],
    config: Any,
    on_progress: ProgressCallback = None,
) -> Any:
    """Compile literal text through UtterPlan's semantic document API."""
    return utterplan.compile_document(
        text,
        input_format=input_format,
        config=config,
        on_progress=on_progress,
    )


def build_render_plan(
    flow_plan: Any,
    profile: Any,
    *,
    previous_plan: Any | None = None,
    preflight: Any | None = None,
) -> Any:
    """Build or replan a RenderPlan through UtterPlan's public projection API."""
    return utterplan.build_render_plan(
        flow_plan,
        profile,
        previous_plan=previous_plan,
        preflight=preflight,
    )
