"""High-level rendering of declarative intents through existing Readio stages."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import audiocompose  # type: ignore[import-not-found]
import utterplan  # type: ignore[import-not-found]

from readio.api.types import (
    ComposeRequest,
    CreateWorkspaceRequest,
    DiagnosticsMode,
    ExportRequest,
    PipelineEvent,
    PipelineResult,
    PipelineStage,
    PlanRequest,
    RenderExecutionOptions,
    RenderIntentPlan,
    RenderIntentResult,
    RenderPlanRequest,
    StatusRequest,
    VoiceRenderRequest,
)
from readio.errors import ReadioError
from readio.integrations.audioexport import preflight_export
from readio.intent import RenderIntent
from readio.intent_resolver import ResolvedRenderIntent
from readio.workspace import WorkspaceRef, inspect_source_kind

if TYPE_CHECKING:
    from readio.api.service import Readio


_STAGE_ORDER: tuple[PipelineStage, ...] = (
    "convert",
    "compile",
    "render_plan",
    "render",
    "compose",
    "export",
)
_STAGE_LABELS = {
    "convert": "convert",
    "compile": "compile",
    "render_plan": "render-plan",
    "render": "render",
    "compose": "compose",
    "export": "export",
}
_STAGE_PACKAGES = {
    "convert": "ssmdconvert",
    "compile": "utterplan",
    "render_plan": "utterplan",
    "render": "voicerender",
    "compose": "audiocompose",
    "export": "audioexport",
}
_STAGE_ACTIONS = {
    "convert": "check the source and bundle path; use --force-stage convert only to replace an existing bundle",
    "compile": "review the selected content and UtterPlan diagnostics; use --restart-from compile to rerun deliberately",
    "render_plan": "check RenderProfile role coverage and review the selected RenderProfile",
    "render": "review VoiceRender's partial artifacts; use --reset-stale-output only when a reset is intended",
    "compose": "inspect rendered artifacts; use --restart-from compose to rebuild deliberately",
    "export": "check AudioExport, FFmpeg, FFprobe, and the final path; use --restart-from export to re-export deliberately",
}


class IntentStageError(ReadioError):
    """A specialist stage failure annotated with render-intent context and next steps."""

    def __init__(
        self,
        stage: str,
        *,
        package: str,
        workspace: Path,
        profile: Path,
        variant: str | None,
        detail: str,
        code: str | None = None,
    ) -> None:
        label = _STAGE_LABELS.get(stage, stage)
        fields = [
            f"Stage: {label}",
            f"Package: {package}",
            f"Bundle: {workspace}",
            f"Profile: {profile}",
        ]
        if variant is not None:
            fields.append(f"Variant: {variant}")
        if code is not None:
            fields.append(f"Code: {code}")
        fields.extend(
            (
                f"Detail: {detail}",
                f"Next: {_STAGE_ACTIONS.get(stage, 'review the intent and specialist output')}",
            )
        )
        super().__init__("render failed\n" + "\n".join(fields))
        self.stage = label
        self.package = package
        self.workspace = workspace
        self.profile = profile
        self.variant = variant
        self.code = code
        self.next_action = _STAGE_ACTIONS.get(stage)


def _forced_stages(options: RenderExecutionOptions) -> set[PipelineStage]:
    selected = set(options.force_stages)
    invalid = selected - set(_STAGE_ORDER)
    if invalid:
        raise ValueError(f"unknown forced stage(s): {', '.join(sorted(invalid))}")
    if options.restart_from is not None:
        if options.restart_from not in _STAGE_ORDER:
            raise ValueError(f"unknown restart stage {options.restart_from!r}")
        start = _STAGE_ORDER.index(options.restart_from)
        selected.update(_STAGE_ORDER[start:])
    return selected


def _planner_config(resolved: ResolvedRenderIntent) -> Any | None:
    planning = resolved.intent.planning
    if resolved.language is None:
        custom = (
            planning.text_preparation != "auto"
            or planning.overlap_mode != "snap"
            or planning.renderability_mode != "repair"
            or planning.spacy != "auto"
            or planning.spacy_model is not None
            or planning.spacy_model_size is not None
            or planning.require_spacy
        )
        if custom:
            raise ValueError(
                "input.language is required when non-default planning controls are set"
            )
        return None

    use_spacy = None if planning.spacy == "auto" else planning.spacy == "on"
    linguistics = utterplan.LinguisticsConfig(
        use_spacy=use_spacy,
        spacy_model=planning.spacy_model,
        spacy_model_size=planning.spacy_model_size,
        require_spacy=planning.require_spacy,
    )
    return utterplan.PlannerConfig(
        language=resolved.language,
        text_preparation=planning.text_preparation,
        overlap_mode=planning.overlap_mode,
        renderability_mode=planning.renderability_mode,
        linguistics=linguistics,
    )


def _compose_options(resolved: ResolvedRenderIntent) -> Any:
    compose = resolved.intent.compose
    return audiocompose.BundleComposeOptions(
        sample_rate=compose.sample_rate,
        clip_policy=compose.clip_policy,
        target_lufs=compose.target_lufs,
        true_peak_ceiling_dbtp=compose.true_peak_ceiling_dbtp,
        peak_policy=compose.peak_policy,
    )


def _is_bundle_source(path: Path) -> bool:
    name = path.name.casefold()
    if name.endswith(".ssmd-bundle.zip"):
        raise ValueError("packed .ssmd-bundle.zip artifacts cannot be rendered as workspaces")
    return name.endswith(".ssmd-bundle")


def _raise_stage_error(
    error: Exception,
    stage: str,
    resolved: ResolvedRenderIntent,
    variant: str | None,
) -> IntentStageError:
    code = getattr(error, "code", None)
    return IntentStageError(
        stage,
        package=_STAGE_PACKAGES[stage],
        workspace=resolved.bundle,
        profile=resolved.profile,
        variant=variant,
        detail=str(error),
        code=code if isinstance(code, str) else None,
    )


def _notify_reuse(
    on_progress: Callable[[PipelineEvent], None] | None, workspace: WorkspaceRef
) -> None:
    if on_progress is not None:
        on_progress(
            PipelineEvent(
                stage="convert",
                current=1,
                total=1,
                message=f"reused validated bundle {workspace.root}",
            )
        )


def plan_render_intent(
    service: Readio,
    intent: RenderIntent,
    options: RenderExecutionOptions | None = None,
    **overrides: Any,
) -> RenderIntentPlan:
    """Resolve and inspect current specialist state without executing pipeline stages."""
    controls = options or RenderExecutionOptions()
    forced = _forced_stages(controls)
    resolved = service.resolve_intent(
        intent,
        check_output_collision=False,
        **overrides,
    )
    source_is_bundle = _is_bundle_source(resolved.source)
    if source_is_bundle and "convert" in forced:
        raise ValueError("--force-stage convert cannot be used when SOURCE is already a bundle")
    if "convert" in forced and inspect_source_kind(resolved.source) != resolved.kind:
        raise ValueError("source kind does not match the intent; refusing to reconvert it")
    preflight_export(resolved.output_format, ffmpeg=controls.ffmpeg, ffprobe=controls.ffprobe)
    _planner_config(resolved)
    _compose_options(resolved)

    bundle_exists = resolved.bundle.exists()
    status = None
    if bundle_exists:
        status = service.status(
            resolved.bundle,
            StatusRequest(
                chapters=resolved.chapters,
                profile=resolved.profile,
                variant=resolved.intent.voice.variant,
            ),
        )
    return RenderIntentPlan(
        resolved=resolved,
        status=status,
        stages=tuple(_STAGE_LABELS[stage] for stage in _STAGE_ORDER),
        convert_required=not bundle_exists or "convert" in forced,
        output_exists=resolved.output.exists(),
    )


def render_intent(
    service: Readio,
    intent: RenderIntent,
    options: RenderExecutionOptions | None = None,
    *,
    on_progress: Callable[[PipelineEvent], None] | None = None,
    **overrides: Any,
) -> RenderIntentResult:
    """Converge a resolved intent through conversion, planning, render, compose, and export."""
    started = time.perf_counter()
    controls = options or RenderExecutionOptions()
    forced = _forced_stages(controls)
    resolved = service.resolve_intent(
        intent,
        check_output_collision=True,
        force_output="export" in forced,
        **overrides,
    )
    source_is_bundle = _is_bundle_source(resolved.source)
    if source_is_bundle and "convert" in forced:
        raise ValueError("--force-stage convert cannot be used when SOURCE is already a bundle")
    if "convert" in forced and inspect_source_kind(resolved.source) != resolved.kind:
        raise ValueError("source kind does not match the intent; refusing to reconvert it")
    preflight_export(resolved.output_format, ffmpeg=controls.ffmpeg, ffprobe=controls.ffprobe)
    planner_config = _planner_config(resolved)
    compose_options = _compose_options(resolved)

    converted = False
    if source_is_bundle:
        workspace = service.resolve_workspace(resolved.source)
        _notify_reuse(on_progress, workspace)
    elif resolved.bundle.exists() and "convert" not in forced:
        workspace = service.resolve_workspace(resolved.bundle)
        _notify_reuse(on_progress, workspace)
    else:
        create_request = CreateWorkspaceRequest(
            source=resolved.source,
            output=resolved.bundle,
            language=resolved.language,
            chapters=resolved.chapters,
            force="convert" in forced,
        )
        try:
            workspace = service.create_workspace(
                resolved.source,
                create_request,
                on_progress=on_progress,
            )
        except Exception as error:
            raise _raise_stage_error(error, "convert", resolved, None) from error
        converted = True

    if workspace.kind != resolved.kind:
        raise ValueError(
            f"resolved bundle kind {workspace.kind!r} does not match intent kind {resolved.kind!r}"
        )

    plan_request = PlanRequest(
        chapters=resolved.chapters,
        language=resolved.language,
        planner_config=planner_config,
        diagnostics=cast(DiagnosticsMode, resolved.intent.planning.diagnostics),
        force="compile" in forced,
    )
    render_plan_request = RenderPlanRequest(
        profile=resolved.profile,
        variant=resolved.intent.voice.variant,
        chapters=resolved.chapters,
        force="render_plan" in forced,
    )
    render_request = VoiceRenderRequest(
        variant="pending",
        chapters=resolved.chapters,
        speech=resolved.intent.render.speech,
        force="render" in forced,
        reset_stale_output=controls.reset_stale_output,
        emphasis_policy=resolved.intent.render.emphasis_policy,
    )
    compose_request = ComposeRequest(
        variant="pending",
        chapters=resolved.chapters,
        options=compose_options,
        force="compose" in forced,
        emphasis_policy=resolved.intent.compose.emphasis_policy,
    )
    export_request = ExportRequest(
        variant="pending",
        chapters=resolved.chapters,
        output=resolved.output,
        format=resolved.output_format,
        bitrate=resolved.intent.output.bitrate,
        sample_rate=resolved.intent.output.sample_rate,
        channels=resolved.intent.output.channels,
        metadata=resolved.intent.output.metadata,
        cover=resolved.intent.output.cover,
        timeline=resolved.intent.output.timeline,
        force="export" in forced,
        ffmpeg=controls.ffmpeg,
        ffprobe=controls.ffprobe,
    )

    try:
        compile_result = service.plan(workspace, plan_request, on_progress=on_progress)
    except Exception as error:
        raise _raise_stage_error(error, "compile", resolved, None) from error
    try:
        projection = service.render_plan(workspace, render_plan_request, on_progress=on_progress)
    except Exception as error:
        raise _raise_stage_error(error, "render_plan", resolved, None) from error

    variant = projection.variant
    try:
        render_result = service.render(
            workspace,
            replace(render_request, variant=variant),
            on_progress=on_progress,
        )
    except Exception as error:
        raise _raise_stage_error(error, "render", resolved, variant) from error
    try:
        compose_result = service.compose(
            workspace,
            replace(compose_request, variant=variant),
            on_progress=on_progress,
        )
    except Exception as error:
        raise _raise_stage_error(error, "compose", resolved, variant) from error
    try:
        export_result = service.export(workspace, replace(export_request, variant=variant))
    except Exception as error:
        raise _raise_stage_error(error, "export", resolved, variant) from error

    pipeline = PipelineResult(
        workspace=workspace,
        compile=compile_result,
        render_plan=projection,
        render=render_result,
        compose=compose_result,
        export=export_result,
        variant=variant,
    )
    return RenderIntentResult(
        workspace=workspace,
        kind=workspace.kind,
        source=resolved.source,
        profile=resolved.profile,
        variant=variant,
        output=resolved.output,
        pipeline=pipeline,
        converted=converted,
        elapsed_seconds=time.perf_counter() - started,
    )


__all__ = ["IntentStageError", "plan_render_intent", "render_intent"]
