"""High-level Readio orchestration over native SSMD workspaces."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from readio.api.types import (
    BookExportRequest,
    ComposeRequest,
    DocumentExportRequest,
    PipelineEvent,
    PipelineResult,
    PlanRequest,
    RenderPlanRequest,
    RunRequest,
    VoiceRenderRequest,
)
from readio.pipeline import compose, export, plan, render, render_plan
from readio.progress import normalize_progress
from readio.workspace import CreateWorkspaceRequest, WorkspaceRef
from readio.workspace import create_workspace as _create_workspace
from readio.workspace import resolve_workspace as _resolve_workspace

ProgressHandler = Callable[[PipelineEvent], None] | None


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
        return render(self._workspace(workspace), request, on_event=on_progress)

    def compose(
        self,
        workspace: WorkspaceRef | str | Path,
        request: ComposeRequest,
        *,
        on_progress: ProgressHandler = None,
    ) -> Any:
        return compose(self._workspace(workspace), request, on_progress=on_progress)

    def export(
        self,
        workspace: WorkspaceRef | str | Path,
        request: DocumentExportRequest | BookExportRequest,
    ) -> Any:
        return export(self._workspace(workspace), request)

    def run(
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
    def _workspace(workspace: WorkspaceRef | str | Path) -> WorkspaceRef:
        return workspace if isinstance(workspace, WorkspaceRef) else _resolve_workspace(workspace)


__all__ = ["Readio"]
