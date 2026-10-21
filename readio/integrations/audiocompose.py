"""AudioCompose public API adapter for SSMD bundle workspaces."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, Literal, cast

import audiocompose  # type: ignore[import-not-found]

from readio.api.types import ComposeRequest
from readio.workspace import WorkspaceRef

ProgressCallback = Callable[[Any], None] | None
ComposeEmphasisPolicy = Literal["segment_fallback", "strict"]


def _emphasis_policy(value: str) -> ComposeEmphasisPolicy:
    if value not in ("segment_fallback", "strict"):
        raise ValueError("emphasis_policy must be 'segment_fallback' or 'strict'")
    return cast(ComposeEmphasisPolicy, value)


def compose_bundle(
    workspace: WorkspaceRef,
    request: ComposeRequest,
    *,
    on_progress: ProgressCallback = None,
) -> Any:
    if request.output_policy is not None and request.options is not None:
        raise ValueError("pass either output_policy or options, not both")

    if request.output_policy is not None:
        if not isinstance(request.output_policy, audiocompose.OutputPolicy):
            raise TypeError("output_policy must be an audiocompose.OutputPolicy")
        output_policy = request.output_policy
        if output_policy.channels != 1:
            raise ValueError("AudioCompose bundle composition supports mono output only")
        options = audiocompose.BundleComposeOptions(
            sample_rate=output_policy.sample_rate,
            clip_policy=output_policy.clip_policy,
            target_lufs=output_policy.loudness.target_lufs,
            true_peak_ceiling_dbtp=output_policy.loudness.true_peak_ceiling_dbtp,
        )
    else:
        options = request.options

    if options is not None and not isinstance(options, audiocompose.BundleComposeOptions):
        raise TypeError("options must be an audiocompose.BundleComposeOptions")
    options = options or audiocompose.BundleComposeOptions()
    options = replace(
        options,
        chapters=request.chapters,
        pauses=request.pause_policy,
        renderplan_root=request.renderplan_root,
        voicerender_root=request.voicerender_root,
        output_root=request.output_root,
        media_root=request.media_root,
        emphasis_policy=_emphasis_policy(request.emphasis_policy),
        save_audiojobs=request.save_audiojobs,
        force=request.force,
        fail_fast=request.fail_fast,
    )
    return audiocompose.compose_bundle(
        workspace.root,
        variant=request.variant,
        options=options,
        on_progress=on_progress,
    )
