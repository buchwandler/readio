"""AudioCompose public API adapters for document and book workspaces."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import audiocompose  # type: ignore[import-not-found]

from readio.api.types import ComposeRequest
from readio.workspace import WorkspaceRef

ProgressCallback = Callable[[Any], None] | None


def compose_document(
    workspace: WorkspaceRef,
    request: ComposeRequest,
    *,
    on_progress: ProgressCallback = None,
) -> Any:
    return audiocompose.compose_document(
        workspace.root,
        variant=request.variant,
        pause_policy=request.pause_policy,
        output_policy=request.output_policy,
        options=request.options,
        renderplan_root=request.renderplan_root,
        voicerender_root=request.voicerender_root,
        output_root=request.output_root,
        media_root=request.media_root,
        save_audiojob=request.save_audiojob,
        force=request.force,
        emphasis_policy=request.emphasis_policy,
        on_progress=on_progress,
    )


def compose_book(
    workspace: WorkspaceRef,
    request: ComposeRequest,
    *,
    on_progress: ProgressCallback = None,
) -> Any:
    return audiocompose.compose_book(
        workspace.root,
        variant=request.variant,
        chapters=request.chapters,
        pause_policy=request.pause_policy,
        output_policy=request.output_policy,
        options=request.options,
        renderplan_root=request.renderplan_root,
        voicerender_root=request.voicerender_root,
        output_root=request.output_root,
        media_root=request.media_root,
        save_audiojobs=request.save_audiojob,
        force=request.force,
        fail_fast=request.fail_fast,
        emphasis_policy=request.emphasis_policy,
        on_progress=on_progress,
    )
