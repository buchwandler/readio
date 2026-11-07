"""VoiceRender public API adapter for SSMD bundle workspaces."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, cast

import voicerender  # type: ignore[import-not-found]

from readio.api.types import VoiceRenderRequest
from readio.workspace import WorkspaceRef

EventCallback = Callable[[Any], None] | None


def _emphasis_policy(value: str) -> Literal["reject", "defer-to-compose"]:
    if value not in ("reject", "defer-to-compose"):
        raise ValueError("emphasis_policy must be 'reject' or 'defer-to-compose'")
    return cast(Literal["reject", "defer-to-compose"], value)


def render_bundle(
    workspace: WorkspaceRef,
    request: VoiceRenderRequest,
    *,
    on_event: EventCallback = None,
) -> Any:
    return voicerender.render_bundle(
        workspace.root,
        variant=request.variant,
        chapters=request.chapters,
        speech=request.speech,
        renderplan_root=request.renderplan_root,
        output_root=request.output_root,
        cache_dir=request.cache_dir,
        force=request.force,
        reset_stale=request.reset_stale_output,
        fail_fast=request.fail_fast,
        emphasis_policy=_emphasis_policy(request.emphasis_policy),
        on_progress=on_event,
    )


def inspect_bundle_render(
    workspace: WorkspaceRef,
    request: VoiceRenderRequest,
) -> Any:
    """Inspect requested persisted render occurrences through VoiceRender."""
    return voicerender.inspect_bundle_render(
        workspace.root,
        variant=request.variant,
        chapters=request.chapters,
        speech=request.speech,
        renderplan_root=request.renderplan_root,
        output_root=request.output_root,
    )


def preflight_render_plan(plan: Any, *, output: Any = None, **options: Any) -> Any:
    """Preflight an in-memory RenderPlan through VoiceRender's canonical engine path."""
    return voicerender.preflight_render_plan(plan, output=output, **options)


def render_renderplan_memory(plan: Any, **options: Any) -> Any:
    """Render selected RenderPlan occurrences without persisting their audio."""
    return voicerender.render_renderplan_memory(plan, **options)


def create_render_profile(**options: Any) -> Any:
    """Create an in-memory RenderProfile through VoiceRender's public API."""
    return voicerender.create_render_profile(**options)


def load_render_profile(profile: Any) -> Any:
    """Load or validate an existing in-memory RenderProfile through VoiceRender."""
    return voicerender.load_render_profile(profile)
