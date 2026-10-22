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
    RenderPlanRequest,
    RunRequest,
    StatusRequest,
    VoiceRenderRequest,
)
from readio.diagnostics import log_event, logger_for, timed_span
from readio.pipeline import compose, export, plan, render, render_plan
from readio.progress import normalize_progress
from readio.status import inspect_status
from readio.workspace import CreateWorkspaceRequest, WorkspaceRef
from readio.workspace import create_workspace as _create_workspace
from readio.workspace import resolve_workspace as _resolve_workspace

ProgressHandler = Callable[[PipelineEvent], None] | None


_LOGGER = logger_for("service")


class Readio:
    """Convenience service for creating, resolving, and running native workspaces."""

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

    def render(
        self,
        workspace: WorkspaceRef | str | Path,
        request: VoiceRenderRequest,
        *,
        on_progress: ProgressHandler = None,
    ) -> Any:
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
