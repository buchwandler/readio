"""High-level Readio orchestration over native SSMD workspaces."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from readio.api.types import (
    BundleStatus,
    ComposeRequest,
    CompositionOptions,
    DiagnosticsMode,
    ExportOptions,
    ExportRequest,
    PipelineEvent,
    PipelineResult,
    PlanningOptions,
    PlanRequest,
    ProjectionOptions,
    RebuildStage,
    RenderExecutionOptions,
    RenderPlanRequest,
    RenderRequest,
    RenderResult,
    RunRequest,
    SourceChapter,
    SourceInspection,
    SpeakRequest,
    SpeakResult,
    StatusRequest,
    VoiceRenderOptions,
    VoiceRenderRequest,
    WorkflowEvent,
    WorkflowPlan,
)
from readio.diagnostics import log_event, logger_for, timed_span
from readio.errors import ReadioError
from readio.integrations.ssmdconvert import inspect_source as _inspect_source_with_ssmdconvert
from readio.intent import RenderIntent
from readio.intent_resolver import ResolvedRenderIntent, resolve_render_intent
from readio.pipeline import compose, export, plan, render, render_plan
from readio.playback import play_audio
from readio.progress import normalize_progress
from readio.render_service import _compose_options, _planner_config
from readio.speech import speak_request as _speak_request
from readio.status import inspect_status
from readio.workspace import (
    CreateWorkspaceRequest,
    PreparationOptions,
    WorkspaceRef,
)
from readio.workspace import create_workspace as _create_workspace
from readio.workspace import resolve_workspace as _resolve_workspace

ProgressHandler = Callable[[PipelineEvent], None] | None


_LOGGER = logger_for("service")


def inspect_source(source: str | Path) -> SourceInspection:
    """Inspect a source or bundle without creating a render workspace."""
    try:
        source_path = Path(source).expanduser().resolve(strict=True)
    except FileNotFoundError:
        raise ReadioError(f"Source not found: {source}") from None
    except Exception as error:  # noqa: BLE001 - normalize source path errors at the public API boundary
        raise ReadioError(f"Cannot resolve source {source}: {error}") from None

    try:
        inspection = _inspect_source_with_ssmdconvert(source_path)
        kind = inspection.kind
        if kind not in {"document", "book"}:
            raise ReadioError(f"Unsupported source kind: {kind!r}")
        value = inspection.value
        metadata = getattr(value, "metadata", {})
        if not isinstance(metadata, Mapping):
            raise ReadioError("Source inspection returned invalid metadata.")
        chapters: tuple[SourceChapter, ...] = ()
        if kind == "book":
            chapters = tuple(
                SourceChapter(
                    number=getattr(chapter, "source_number", index),
                    title=str(getattr(chapter, "title", None) or f"Chapter {index}"),
                    source_id=getattr(chapter, "source_id", None) or getattr(chapter, "id", None),
                    parent_id=getattr(chapter, "source_parent_id", None)
                    or getattr(chapter, "parent_id", None),
                    level=getattr(chapter, "level", None),
                    char_count=getattr(chapter, "char_count", None),
                )
                for index, chapter in enumerate(getattr(value, "chapters", ()), start=1)
            )
        return SourceInspection(
            source=source_path,
            kind=kind,
            metadata=MappingProxyType(dict(metadata)),
            chapters=chapters,
        )
    except ReadioError:
        raise
    except Exception as error:  # noqa: BLE001 - hide specialist traceback behind a Readio error
        raise ReadioError(f"Cannot inspect source {source_path}: {error}") from None


def render_request_from_intent(
    intent: RenderIntent,
    *,
    config_path: str | Path | None = None,
    source: str | Path | None = None,
    bundle: str | Path | None = None,
    profile: str | Path | None = None,
    variant: str | None = None,
    chapters: str | None = None,
    language: str | None = None,
    output_format: str | None = None,
    output: str | Path | None = None,
    rebuild_from: RebuildStage | None = None,
    replace_output: bool = False,
    ffmpeg: Path | None = None,
    ffprobe: Path | None = None,
) -> RenderRequest:
    """Resolve a persisted intent into the canonical file-to-audio request."""
    resolved = resolve_render_intent(
        intent,
        config_path=config_path,
        source=source,
        bundle=bundle,
        profile=profile,
        variant=variant,
        chapters=chapters,
        language=language,
        output_format=output_format,
        output=output,
        check_output_collision=False,
    )
    return RenderRequest(
        source=resolved.source,
        profile=resolved.profile,
        chapters=resolved.chapters,
        language=resolved.language,
        bundle=resolved.bundle,
        variant=resolved.intent.voice.variant,
        output=resolved.output,
        format=resolved.output_format,
        bitrate=resolved.intent.output.bitrate,
        sample_rate=resolved.intent.output.sample_rate,
        channels=resolved.intent.output.channels,
        rebuild_from=rebuild_from,
        replace_output=replace_output,
        planning=PlanningOptions(
            planner_config=_planner_config(resolved),
            diagnostics=cast(DiagnosticsMode, resolved.intent.planning.diagnostics),
        ),
        projection=ProjectionOptions(
            pause_timing=resolved.intent.projection.pause_timing,
        ),
        preparation=PreparationOptions(
            speech_semantics=resolved.intent.preparation.speech_semantics,
            spacy=resolved.intent.preparation.spacy or "off",
            speech_profile=resolved.intent.preparation.speech_profile,
            pronunciations=resolved.intent.preparation.pronunciations,
        ),
        voice_render=VoiceRenderOptions(
            speech=resolved.intent.render.speech,
            emphasis_policy=resolved.intent.render.emphasis_policy,
        ),
        composition=CompositionOptions(
            options=_compose_options(resolved),
            emphasis_policy=resolved.intent.compose.emphasis_policy,
        ),
        export=ExportOptions(
            metadata=resolved.intent.output.metadata,
            cover=resolved.intent.output.cover,
            timeline=resolved.intent.output.timeline,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        ),
    )


_INTENT_STAGE_ORDER: tuple[RebuildStage, ...] = (
    "convert",
    "compile",
    "render_plan",
    "render",
    "compose",
    "export",
)


def _with_execution_options(
    request: RenderRequest,
    options: RenderExecutionOptions | None,
) -> RenderRequest:
    if options is None:
        return request

    selected = set(options.force_stages)
    if options.restart_from is not None:
        selected.add(options.restart_from)
    if options.reset_stale_output:
        selected.add("render")
    invalid = selected - set(_INTENT_STAGE_ORDER)
    if invalid:
        raise ValueError(f"unknown forced stage(s): {', '.join(sorted(invalid))}")

    starts = selected | ({request.rebuild_from} if request.rebuild_from is not None else set())
    rebuild_from = min(starts, key=_INTENT_STAGE_ORDER.index) if starts else None
    return replace(
        request,
        rebuild_from=rebuild_from,
        replace_output=request.replace_output or bool(selected),
        export=replace(
            request.export,
            ffmpeg=options.ffmpeg or request.export.ffmpeg,
            ffprobe=options.ffprobe or request.export.ffprobe,
        ),
    )


class Readio:
    """Convenience gateway for unified and specialist-backed Readio workflows."""

    def inspect_source(self, source: str | Path) -> SourceInspection:
        """Return normalized, read-only source metadata and chapter inventory."""
        return inspect_source(source)

    def resolve_workspace(self, path: str | Path) -> WorkspaceRef:
        return _resolve_workspace(path)

    def create_workspace(
        self,
        source: str | Path,
        request: CreateWorkspaceRequest | None = None,
        *,
        on_progress: ProgressHandler = None,
    ) -> WorkspaceRef:
        with timed_span(_LOGGER, "stage", stage="convert", source=str(source)):
            return _create_workspace(
                source,
                request,
                on_progress=normalize_progress("convert", on_progress),
            )

    def ensure_workspace(
        self,
        source: str | Path,
        request: CreateWorkspaceRequest | None = None,
        *,
        on_progress: ProgressHandler = None,
    ) -> WorkspaceRef:
        return self.create_workspace(source, request, on_progress=on_progress)

    def speak(
        self,
        request: SpeakRequest,
        *,
        play: bool = True,
        on_progress: Callable[[WorkflowEvent], None] | None = None,
    ) -> SpeakResult:
        """Synthesize semantic text entirely in memory and optionally play the result."""
        with timed_span(
            _LOGGER,
            "stage",
            stage="speak",
            profile=str(request.profile) if request.profile is not None else None,
            role=request.role,
            engine=request.engine,
            target=request.target,
        ):
            result = _speak_request(request, on_progress=on_progress)
        if play:
            play_audio(result.audio.audio, result.audio.sample_rate)
        return result

    def plan(
        self,
        workspace: WorkspaceRef | str | Path,
        request: PlanRequest,
        *,
        on_progress: ProgressHandler = None,
    ) -> Any:
        with timed_span(
            _LOGGER,
            "stage",
            stage="compile",
            workspace=str(self._root(workspace)),
            chapters=request.chapters,
            language=request.language,
        ):
            return plan(
                self._workspace(workspace),
                request,
                on_start=on_progress,
                on_progress=on_progress,
            )

    def render_plan(
        self,
        workspace: WorkspaceRef | str | Path,
        request: RenderPlanRequest,
        *,
        on_progress: ProgressHandler = None,
    ) -> Any:
        with timed_span(
            _LOGGER,
            "stage",
            stage="render_plan",
            workspace=str(self._root(workspace)),
            chapters=request.chapters,
            profile=request.profile,
            variant=request.variant,
            output_root=request.output_root,
        ):
            return render_plan(
                self._workspace(workspace),
                request,
                on_start=on_progress,
                on_progress=on_progress,
            )

    def inspect(self, request: RenderRequest) -> WorkflowPlan:
        """Inspect a complete file-to-audio workflow without making changes."""
        from readio.render_workflow import inspect_render_workflow

        return inspect_render_workflow(request)

    def render(
        self,
        workspace: WorkspaceRef | str | Path | RenderRequest,
        request: VoiceRenderRequest | None = None,
        *,
        dry_run: bool = False,
        on_progress: Callable[[Any], None] | None = None,
    ) -> Any:
        """Execute a complete RenderRequest, or the legacy VoiceRender stage adapter."""
        if isinstance(workspace, RenderRequest):
            if request is not None:
                raise TypeError("a RenderRequest cannot be combined with a stage request")
            from readio.render_workflow import render_workflow

            with timed_span(
                _LOGGER,
                "workflow.render",
                source=str(workspace.source),
                dry_run=dry_run,
            ):
                return render_workflow(self, workspace, dry_run=dry_run, on_progress=on_progress)
        if request is None:
            raise TypeError("the VoiceRender stage API requires a VoiceRenderRequest")
        if dry_run:
            raise TypeError("dry_run is only supported with a file-to-audio RenderRequest")
        with timed_span(
            _LOGGER,
            "stage",
            stage="render",
            workspace=str(self._root(workspace)),
            variant=request.variant,
            output_root=request.output_root,
            cache_dir=request.cache_dir,
        ):
            return render(self._workspace(workspace), request, on_event=on_progress)

    def compose(
        self,
        workspace: WorkspaceRef | str | Path,
        request: ComposeRequest,
        *,
        on_progress: ProgressHandler = None,
    ) -> Any:
        with timed_span(
            _LOGGER,
            "stage",
            stage="compose",
            workspace=str(self._root(workspace)),
            variant=request.variant,
            output_root=request.output_root,
            voicerender_root=request.voicerender_root,
        ):
            return compose(self._workspace(workspace), request, on_progress=on_progress)

    def export(
        self,
        workspace: WorkspaceRef | str | Path,
        request: ExportRequest,
    ) -> Any:
        with timed_span(
            _LOGGER,
            "stage",
            stage="export",
            workspace=str(self._workspace(workspace).root),
            variant=request.variant,
            output=request.output,
            output_root=request.output_root,
        ):
            return export(self._workspace(workspace), request)

    def status(
        self,
        workspace: WorkspaceRef | str | Path,
        request: StatusRequest | None = None,
    ) -> BundleStatus:
        resolved = self._workspace(workspace)
        selected = request or StatusRequest()
        log_event(
            _LOGGER,
            logging.DEBUG,
            "status.request",
            workspace=resolved.root,
            kind=resolved.kind,
            chapters=selected.chapters,
            profile=selected.profile,
            variant=selected.variant,
        )
        with timed_span(
            _LOGGER,
            "stage",
            stage="status",
            workspace=str(resolved.root),
            chapters=selected.chapters,
            variant=selected.variant,
        ):
            return inspect_status(resolved, selected)

    def resolve_intent(self, intent: RenderIntent, **overrides: Any) -> ResolvedRenderIntent:
        """Resolve a render intent and apply explicit path/value overrides."""
        return resolve_render_intent(intent, **overrides)

    def request_from_intent(
        self,
        intent: RenderIntent,
        *,
        config_path: str | Path | None = None,
        **overrides: Any,
    ) -> RenderRequest:
        """Resolve a saved render intent into a canonical RenderRequest."""
        return render_request_from_intent(intent, config_path=config_path, **overrides)

    def inspect_intent(
        self,
        intent: RenderIntent,
        *,
        config_path: str | Path | None = None,
        **overrides: Any,
    ) -> WorkflowPlan:
        """Inspect a persisted intent through the canonical RenderRequest workflow."""
        request = self.request_from_intent(intent, config_path=config_path, **overrides)
        return self.inspect(request)

    def plan_render_intent(
        self,
        intent: RenderIntent,
        options: RenderExecutionOptions | None = None,
        **overrides: Any,
    ) -> WorkflowPlan:
        """Compatibility alias for inspecting an intent-derived workflow request."""
        request = self.request_from_intent(intent, **overrides)
        return self.inspect(_with_execution_options(request, options))

    def render_intent(
        self,
        intent: RenderIntent,
        options: RenderExecutionOptions | None = None,
        *,
        dry_run: bool = False,
        on_progress: Callable[[Any], None] | None = None,
        **overrides: Any,
    ) -> RenderResult | WorkflowPlan:
        """Render an intent through the canonical RenderRequest workflow."""
        request = self.request_from_intent(intent, **overrides)
        return self.render(
            _with_execution_options(request, options),
            dry_run=dry_run,
            on_progress=on_progress,
        )

    def run(
        self,
        source_or_workspace: str | Path,
        request: RunRequest,
        *,
        on_progress: ProgressHandler = None,
    ) -> PipelineResult:
        with timed_span(
            _LOGGER,
            "pipeline",
            source=str(source_or_workspace),
            chapters=request.plan.chapters,
            export=request.export is not None,
        ):
            return self._run_pipeline(source_or_workspace, request, on_progress=on_progress)

    def _run_pipeline(
        self,
        source_or_workspace: str | Path,
        request: RunRequest,
        *,
        on_progress: ProgressHandler = None,
    ) -> PipelineResult:
        workspace = self.ensure_workspace(
            source_or_workspace,
            request.create,
            on_progress=on_progress,
        )
        compile_result = self.plan(workspace, request.plan, on_progress=on_progress)
        projection = self.render_plan(workspace, request.render_plan, on_progress=on_progress)
        variant = projection.variant
        render_result = self.render(
            workspace,
            replace(request.render, variant=variant),
            on_progress=on_progress,
        )
        compose_result = self.compose(
            workspace,
            replace(request.compose, variant=variant),
            on_progress=on_progress,
        )
        export_result = None
        if request.export is not None:
            export_result = self.export(
                workspace,
                replace(request.export, variant=variant),
            )
        return PipelineResult(
            workspace=workspace,
            compile=compile_result,
            render_plan=projection,
            render=render_result,
            compose=compose_result,
            export=export_result,
            variant=variant,
        )

    @staticmethod
    def _root(workspace: WorkspaceRef | str | Path) -> Path:
        return workspace.root if isinstance(workspace, WorkspaceRef) else Path(workspace)

    @staticmethod
    def _workspace(workspace: WorkspaceRef | str | Path) -> WorkspaceRef:
        return workspace if isinstance(workspace, WorkspaceRef) else _resolve_workspace(workspace)


__all__ = ["Readio"]
