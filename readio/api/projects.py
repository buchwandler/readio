"""Persistent project workflows exposed through :mod:`readio.api`."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .. import project as project_internal
from ..jsonutil import JsonValue, json_value
from ..project_model import ProjectFormatError as InternalProjectFormatError
from ..stages.composition import compose_project
from ..stages.export import export_project
from ..stages.pipeline import _project_request, build_project, preview_project, project_status
from ..stages.planning import plan_project
from ..stages.synthesis import synthesize_project
from .errors import (
    ExecutionError,
    ProjectConflictError,
    ProjectError,
    ProjectFormatError,
    ProjectNotFoundError,
    ReadioError,
    translate_exception,
)
from .events import EventHandler, ReadioEvent, compose_event_handlers
from .types import (
    CompositionOptions,
    Diagnostic,
    ExportOptions,
    NextAction,
    PreviewRequest,
    PreviewResult,
    ProjectBuildRequest,
    ProjectBuildResult,
    ProjectCompositionResult,
    ProjectExportResult,
    ProjectLike,
    ProjectPlanResult,
    ProjectPlanScope,
    ProjectRef,
    ProjectStatus,
    ProjectSynthesisResult,
    StageName,
    StageOperation,
    StageStatus,
    SynthesisRequest,
)

if TYPE_CHECKING:
    from .app import Readio


_STATUS_DETAIL_KEYS = frozenset(
    {
        "sha256",
        "scope_id",
        "plan_id",
        "plan_ids",
        "reusable",
        "required",
        "missing",
        "total",
        "scopes",
        "per_scope",
        "profile_id",
        "composition_id",
        "format",
    }
)
_OPERATION_DETAIL_KEYS = frozenset(
    {
        "scopes",
        "reused",
        "rendered",
        "profile_id",
        "composition_id",
        "frames",
        "items",
        "format",
        "output_sha256",
        "export_id",
    }
)


class ProjectService:
    """Create, inspect, plan, and incrementally build persistent projects."""

    def __init__(self, app: Readio) -> None:
        self._app = app

    def create(self, source: Path, *, output: Path | None = None) -> ProjectRef:
        project = self._call(lambda: project_internal.init_project(source, output))
        return self._ref(project)

    def open(self, path: Path | str | None = None) -> ProjectRef:
        return self._ref(self._call(lambda: project_internal.load_project(path)))

    def find(self, path: Path | str | None = None) -> ProjectRef | None:
        root = self._call(lambda: project_internal.find_project(path))
        if root is None:
            return None
        return self._ref(self._call(lambda: project_internal.load_project(root)))

    def status(self, project: ProjectLike) -> ProjectStatus:
        internal = self._load(project)
        raw = self._call(lambda: project_status(internal))
        stages = tuple(
            StageStatus(
                stage=cast(StageName, row["stage"]),
                state=cast(str, row["state"]),
                reason=str(row["reason"]),
                blocked_by=cast(StageName | None, row.get("blocked_by")),
                details=self._safe_details(row, _STATUS_DETAIL_KEYS),
            )
            for row in raw["stages"]
        )
        issues = tuple(
            Diagnostic(
                code=str(row["code"]),
                severity="error" if "invalid" in str(row["code"]) else "warning",
                message=str(row["message"]),
                field=str(row["stage"]),
                details={"reason": str(row["code"])},
            )
            for row in raw["issues"]
        )
        actions = tuple(
            NextAction(stage=cast(StageName, row["stage"]), reason=str(row["reason"]))
            for row in raw["next_actions"]
        )
        return ProjectStatus(self._ref(internal), stages, issues, actions)

    def plan(
        self,
        project: ProjectLike,
        *,
        on_event: EventHandler | None = None,
    ) -> ProjectPlanResult:
        internal = self._load(project)
        handler = self._handler(on_event)
        operation = "projects.plan"
        self._notify(handler, ReadioEvent(kind="operation.started", operation=operation))
        self._notify(handler, ReadioEvent(kind="stage.started", operation=operation, stage="plan"))
        result = self._call(lambda: plan_project(internal, self._app.config))
        self._notify(
            handler,
            ReadioEvent(
                kind="stage.completed",
                operation=operation,
                stage="plan",
                details={"scope_count": len(result.scopes)},
            ),
        )
        self._notify(handler, ReadioEvent(kind="operation.completed", operation=operation))
        scopes = tuple(
            ProjectPlanScope(
                scope_id=planned.scope.id,
                plan_id=planned.compiled.plan_id,
                sha256=planned.compiled.sha256,
                units=len(planned.compiled.plan.units),
            )
            for planned in result.scopes
        )
        return ProjectPlanResult(self._ref(internal), scopes)

    def synthesize(
        self,
        project: ProjectLike,
        request: SynthesisRequest | None = None,
        *,
        selection: str = "all",
        voice_bindings: Mapping[str, str] | None = None,
        activate: bool = True,
        on_event: EventHandler | None = None,
    ) -> ProjectSynthesisResult:
        internal = self._load(project)
        request = request or SynthesisRequest()
        handler = self._handler(on_event)
        operation = "projects.synthesize"
        self._notify(handler, ReadioEvent(kind="operation.started", operation=operation))
        self._notify(
            handler, ReadioEvent(kind="stage.started", operation=operation, stage="synthesis")
        )
        raw = self._call(
            lambda: synthesize_project(
                internal,
                self._app.config,
                request=_project_request(
                    internal,
                    self._app.config,
                    request,
                    voice_bindings=voice_bindings,
                ),
                selector=selection,
                activate=activate,
                on_event=self._synthesis_handler(handler, operation),
            )
        )
        self._notify(
            handler,
            ReadioEvent(
                kind="stage.completed",
                operation=operation,
                stage="synthesis",
                details={"reused": raw["reused"], "rendered": raw["rendered"]},
            ),
        )
        selection = raw["selection"]
        selected_units = (
            sum(len(scope.unit_indices) for scope in selection.scopes)
            if hasattr(selection, "scopes")
            else len(selection.unit_indices)
        )
        self._notify(handler, ReadioEvent(kind="operation.completed", operation=operation))
        return ProjectSynthesisResult(
            project=self._ref(internal),
            profile_id=raw["profile"].profile_id,
            plan_ids=self._plan_ids(raw["plan_ids"]),
            reused=int(raw["reused"]),
            rendered=int(raw["rendered"]),
            activated=bool(raw["activated"]),
            selected_units=selected_units,
        )

    def compose(
        self,
        project: ProjectLike,
        options: CompositionOptions | None = None,
        *,
        on_event: EventHandler | None = None,
    ) -> ProjectCompositionResult:
        internal = self._load(project)
        options = options or CompositionOptions()
        handler = self._handler(on_event)
        operation = "projects.compose"
        self._notify(handler, ReadioEvent(kind="operation.started", operation=operation))
        self._notify(
            handler, ReadioEvent(kind="stage.started", operation=operation, stage="composition")
        )
        raw = self._call(
            lambda: compose_project(
                internal,
                target_lufs=options.target_lufs,
                true_peak_ceiling_dbtp=options.true_peak_ceiling_dbtp,
                peak_policy=options.peak_policy,
                clip_policy=options.clip_policy,
                on_progress=self._composition_handler(handler, operation),
                on_phase=self._phase_handler(handler, operation),
            )
        )
        self._notify(
            handler,
            ReadioEvent(
                kind="stage.completed",
                operation=operation,
                stage="composition",
                details={"frames": raw["frames"], "items": raw["items"]},
            ),
        )
        self._notify(handler, ReadioEvent(kind="operation.completed", operation=operation))
        return ProjectCompositionResult(
            project=self._ref(internal),
            composition_id=str(raw["composition_id"]),
            frames=int(raw["frames"]),
            items=int(raw["items"]),
            master_path=Path(raw["master"]),
        )

    def export(
        self,
        project: ProjectLike,
        options: ExportOptions | None = None,
        *,
        on_event: EventHandler | None = None,
    ) -> ProjectExportResult:
        internal = self._load(project)
        options = options or ExportOptions()
        handler = self._handler(on_event)
        operation = "projects.export"
        self._notify(handler, ReadioEvent(kind="operation.started", operation=operation))
        self._notify(
            handler, ReadioEvent(kind="stage.started", operation=operation, stage="export")
        )
        raw = self._call(
            lambda: export_project(
                internal,
                audio_format=options.format,
                bitrate=options.bitrate,
                output=options.output,
            )
        )
        self._notify(
            handler, ReadioEvent(kind="stage.completed", operation=operation, stage="export")
        )
        self._notify(handler, ReadioEvent(kind="operation.completed", operation=operation))
        return ProjectExportResult(
            project=self._ref(internal),
            output_path=Path(raw["path"]),
            format=str(raw["format"]),
            output_sha256=str(raw["output_sha256"]),
        )

    def build(
        self,
        project: ProjectLike,
        request: ProjectBuildRequest | None = None,
        *,
        on_event: EventHandler | None = None,
    ) -> ProjectBuildResult:
        internal = self._load(project)
        request = request or ProjectBuildRequest()
        handler = self._handler(on_event)
        operation = "projects.build"
        self._notify(handler, ReadioEvent(kind="operation.started", operation=operation))
        raw = self._call(
            lambda: build_project(
                internal,
                self._app.config,
                request,
                on_synthesis_event=self._synthesis_handler(handler, operation),
                on_composition_progress=self._composition_handler(handler, operation),
                on_phase=self._phase_handler(handler, operation),
                on_stage=lambda stage, state: self._build_stage_event(
                    handler, operation, stage, state
                ),
            )
        )
        self._notify(handler, ReadioEvent(kind="operation.completed", operation=operation))
        operations = tuple(
            StageOperation(
                stage=str(row["stage"]),
                action=cast(
                    str,
                    row["action"]
                    if row["action"] in {"skipped", "rebuilt", "reused", "created"}
                    else "rebuilt",
                ),
                details=self._safe_details(row, _OPERATION_DETAIL_KEYS),
            )
            for row in raw["operations"]
        )
        output = raw.get("output_path")
        return ProjectBuildResult(
            project=self._ref(internal),
            operations=operations,
            output_path=Path(output) if output is not None else None,
        )

    def preview(
        self,
        project: ProjectLike,
        request: PreviewRequest | None = None,
        *,
        on_event: EventHandler | None = None,
    ) -> PreviewResult:
        internal = self._load(project)
        request = request or PreviewRequest()
        handler = self._handler(on_event)
        operation = "projects.preview"
        self._notify(handler, ReadioEvent(kind="operation.started", operation=operation))
        self._notify(
            handler, ReadioEvent(kind="stage.started", operation=operation, stage="synthesis")
        )
        composition_started = False
        composition_completed = False

        def synthesis_event(event: object) -> None:
            if getattr(event, "kind", None) == "complete":
                self._notify(
                    handler,
                    ReadioEvent(kind="stage.completed", operation=operation, stage="synthesis"),
                )
            self._forward_synthesis_event(handler, operation, event)

        def composition_event(event: object) -> None:
            nonlocal composition_started, composition_completed
            kind = getattr(event, "kind", None)
            if kind == "compose_started" and not composition_started:
                composition_started = True
                self._notify(
                    handler,
                    ReadioEvent(kind="stage.started", operation=operation, stage="composition"),
                )
            if kind == "compose_completed" and not composition_completed:
                composition_completed = True
                self._notify(
                    handler,
                    ReadioEvent(kind="stage.completed", operation=operation, stage="composition"),
                )
            self._forward_composition_event(handler, operation, event)

        raw = self._call(
            lambda: preview_project(
                internal,
                self._app.config,
                request=_project_request(
                    internal,
                    self._app.config,
                    request.synthesis,
                    voice_bindings=request.voice_bindings,
                ),
                selector=request.selection,
                output=request.output,
                composition=request.composition,
                activate=request.activate,
                on_event=synthesis_event,
                on_composition_progress=composition_event,
                on_phase=self._phase_handler(handler, operation),
            )
        )
        if not composition_started:
            self._notify(
                handler, ReadioEvent(kind="stage.started", operation=operation, stage="composition")
            )
        if not composition_completed:
            self._notify(
                handler,
                ReadioEvent(kind="stage.completed", operation=operation, stage="composition"),
            )
        self._notify(handler, ReadioEvent(kind="operation.completed", operation=operation))
        return PreviewResult(
            project=self._ref(internal),
            profile_id=str(raw["profile_id"]),
            plan_ids=self._plan_ids(raw["plan_ids"]),
            reused=int(raw["reused"]),
            rendered=int(raw["rendered"]),
            activated=bool(raw["activated"]),
            sample_rate=int(raw["sample_rate"]),
            frames=int(raw["frames"]),
            items=int(raw["items"]),
            output_path=Path(raw["output"]) if raw.get("output") is not None else None,
            composition_id=str(raw["composition_id"]) if raw.get("composition_id") else None,
        )

    def _load(self, project: ProjectLike) -> project_internal.Project:
        path = project.root if isinstance(project, ProjectRef) else project
        return self._call(lambda: project_internal.load_project(path))

    def _ref(self, project: project_internal.Project) -> ProjectRef:
        manifest = project.manifest
        return ProjectRef(
            root=project.root,
            project_id=manifest.project_id,
            name=manifest.name,
            kind=manifest.kind,
            source_format=manifest.source_format,
        )

    def _handler(self, on_event: EventHandler | None) -> EventHandler | None:
        return compose_event_handlers(self._app.on_event, on_event)

    def _notify(self, handler: EventHandler | None, event: ReadioEvent) -> None:
        if handler is None:
            return
        try:
            handler(event)
        except ReadioError:
            raise
        except Exception as error:
            raise translate_exception(
                error,
                error_type=ExecutionError,
                code="event.handler_failed",
            ) from error

    def _synthesis_handler(self, handler: EventHandler | None, operation: str):
        if handler is None:
            return None
        return lambda event: self._forward_synthesis_event(handler, operation, event)

    def _forward_synthesis_event(
        self, handler: EventHandler | None, operation: str, event: object
    ) -> None:
        details = getattr(event, "details", {})
        safe = self._safe_details(
            details, _STATUS_DETAIL_KEYS | {"engine", "provider", "routing_mode"}
        )
        self._notify(
            handler,
            ReadioEvent(
                kind="progress",
                operation=operation,
                stage="synthesis",
                message=str(getattr(event, "kind", "synthesis")),
                completed=getattr(event, "completed", None),
                total=getattr(event, "total", None),
                scope_id=getattr(event, "scope_id", None),
                unit_id=getattr(event, "unit_id", None),
                segment_id=getattr(event, "segment_id", None),
                details=safe,
            ),
        )

    def _composition_handler(self, handler: EventHandler | None, operation: str):
        if handler is None:
            return None
        return lambda event: self._forward_composition_event(handler, operation, event)

    def _forward_composition_event(
        self, handler: EventHandler | None, operation: str, event: object
    ) -> None:
        details = getattr(event, "details", {})
        safe = self._safe_details(
            details,
            frozenset(
                {
                    "item_id",
                    "item_kind",
                    "completed_items",
                    "total_items",
                    "phase",
                    "clip_items",
                    "metadata_kinds",
                }
            ),
        )
        self._notify(
            handler,
            ReadioEvent(
                kind="progress",
                operation=operation,
                stage="composition",
                message=str(getattr(event, "kind", "composition")),
                completed=getattr(event, "completed_items", None),
                total=getattr(event, "total_items", None),
                sample_count=getattr(event, "output_frames", None),
                sample_rate=getattr(event, "target_sample_rate", None),
                audio_seconds=getattr(event, "completed_audio_seconds", None),
                total_audio_seconds=getattr(event, "total_audio_seconds", None),
                details=safe,
            ),
        )

    def _phase_handler(self, handler: EventHandler | None, operation: str):
        if handler is None:
            return None

        def phase(message: str) -> None:
            self._notify(
                handler,
                ReadioEvent(
                    kind="stage.started",
                    operation=operation,
                    stage="composition",
                    message=message,
                ),
            )

        return phase

    def _build_stage_event(
        self, handler: EventHandler | None, operation: str, stage: str, state: str
    ) -> None:
        if state == "started":
            self._notify(
                handler,
                ReadioEvent(kind="stage.started", operation=operation, stage=stage),
            )
        else:
            self._notify(
                handler,
                ReadioEvent(
                    kind="stage.completed",
                    operation=operation,
                    stage=stage,
                    details={"action": state},
                ),
            )

    def _safe_details(
        self,
        values: Mapping[str, object],
        allowed: frozenset[str] = _STATUS_DETAIL_KEYS,
    ) -> Mapping[str, JsonValue]:
        return {key: json_value(value) for key, value in values.items() if key in allowed}

    def _plan_ids(self, rows: object) -> tuple[ProjectPlanScope, ...]:
        return tuple(
            ProjectPlanScope(
                scope_id=str(row["scope_id"]),
                plan_id=str(row["plan_id"]),
            )
            for row in cast(tuple[Mapping[str, object], ...], rows)
        )

    def _call(self, callback):
        try:
            return callback()
        except ReadioError:
            raise
        except InternalProjectFormatError as error:
            raise ProjectFormatError(str(error), code="project.invalid") from error
        except project_internal.ProjectError as error:
            message = str(error)
            if "locked" in message.lower():
                raise ProjectConflictError(message, code="project.locked") from error
            if "not a Readio project" in message:
                raise ProjectNotFoundError(message, code="project.not_found") from error
            if "already exists" in message:
                raise ProjectConflictError(message, code="project.conflict") from error
            raise ProjectError(message, code="project.invalid") from error
        except (TypeError, ValueError, KeyError) as error:
            raise translate_exception(error) from error
        except Exception as error:
            raise translate_exception(
                error,
                error_type=ProjectError,
                code="project.operation_failed",
            ) from error


__all__ = ["ProjectService"]
