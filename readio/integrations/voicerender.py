"""VoiceRender public API adapter for SSMD bundle workspaces."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import voicerender  # type: ignore[import-not-found]

from readio.api.types import VoiceRenderRequest
from readio.workspace import WorkspaceRef

EventCallback = Callable[[Any], None] | None


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
        emphasis_policy=request.emphasis_policy,
        on_progress=on_event,
    )
