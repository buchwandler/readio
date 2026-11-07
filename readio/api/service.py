"""High-level Readio orchestration over native SSMD workspaces."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from readio.api.types import (
    BundleStatus,
    ComposeRequest,
    ExportRequest,
    PipelineEvent,
    PipelineResult,
    PlanRequest,
    RenderExecutionOptions,
    RenderIntentPlan,
    RenderIntentResult,
    RenderPlanRequest,
    RenderRequest,
    RunRequest,
    SpeakRequest,
    SpeakResult,
    StatusRequest,
    VoiceRenderRequest,
    WorkflowEvent,
    WorkflowPlan,
)
from readio.diagnostics import log_event, logger_for, timed_span
from readio.intent import RenderIntent
from readio.intent_resolver import ResolvedRenderIntent, resolve_render_intent
from readio.pipeline import compose, export, plan, render, render_plan
from readio.playback import play_audio
from readio.progress import normalize_progress
from readio.render_service import (
    plan_render_intent as _plan_render_intent,
)
from readio.render_service import (
    render_intent as _render_intent,
)
from readio.speech import speak_request as _speak_request
from readio.status import inspect_status
from readio.workspace import CreateWorkspaceRequest, WorkspaceRef
from readio.workspace import create_workspace as _create_workspace
from readio.workspace import resolve_workspace as _resolve_workspace

ProgressHandler = Callable[[PipelineEvent], None] | None


_LOGGER = logger_for("service")


class Readio:
    """Convenience gateway for unified and specialist-backed Readio workflows."""

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

    def plan_render_intent(
        self,
        intent: RenderIntent,
        options: RenderExecutionOptions | None = None,
        **overrides: Any,
    ) -> RenderIntentPlan:
        """Resolve an intent and inspect current state without running stages."""
        return _plan_render_intent(self, intent, options, **overrides)

    def render_intent(
        self,
        intent: RenderIntent,
        options: RenderExecutionOptions | None = None,
        *,
        on_progress: ProgressHandler = None,
        **overrides: Any,
    ) -> RenderIntentResult:
        """Execute a complete intent through the existing specialist stage APIs."""
        return _render_intent(
            self,
            intent,
            options,
            on_progress=on_progress,
            **overrides,
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
