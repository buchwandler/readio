"""Persistent project workflows exposed through :mod:`readio.api`."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from .. import project as project_internal
from ..jsonutil import JsonValue, json_value
from ..project_model import ProjectFormatError as InternalProjectFormatError
from ..project_model import ProjectManifest
from ..project_settings import (
    apply_project_settings_patch,
    project_settings_from_manifest,
    project_synthesis_request,
    with_project_settings,
)
from ..stages.composition import CompositionProgress, compose_project
from ..stages.export import export_project
from ..stages.pipeline import _project_request, build_project, preview_project, project_status
from ..stages.planning import PlanningProgressCallback, ProjectPlanningProgress, plan_project
from ..stages.synthesis import resolve_project_synthesis, synthesize_project
from .errors import (
    ExecutionError,
    ProjectConflictError,
    ProjectError,
    ProjectFormatError,
    ProjectNotFoundError,
    ReadioError,
    translate_exception,
)
from .events import (
    EventHandler,
    EventStage,
    ProgressKind,
    ReadioEvent,
    compose_event_handlers,
)
from .types import (
    CompositionOptions,
    Diagnostic,
    ExportOptions,
    LoudnessSummary,
    NextAction,
    PreviewRequest,
    PreviewResult,
    ProjectBuildRequest,
    ProjectBuildResult,
    ProjectCompositionResult,
    ProjectExportResult,
    ProjectLike,
    ProjectPlanAttemptRef,
    ProjectPlanAttemptScope,
    ProjectPlanInspection,
    ProjectPlanInspectionOptions,
    ProjectPlanIssue,
    ProjectPlanOptions,
    ProjectPlanRepairOptions,
    ProjectPlanRepairResult,
    ProjectPlanResult,
    ProjectPlanScope,
    ProjectRef,
    ProjectSettings,
    ProjectSettingsPatch,
    ProjectStatus,
    ProjectSynthesisArtifact,
    ProjectSynthesisResult,
    StageName,
    StageOperation,
    StageStatus,
    SynthesisRequest,
    SynthesisResolution,
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
        "workspace",
        "profile_id",
        "composition_id",
        "validation_code",
        "validation_path",
        "action",
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
        "loudness",
        "mastering_profile",
        "warnings",
        "output_sha256",
        "export_id",
    }
)


def _optional_string_tuple(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, (tuple, list)):
        return tuple(str(item) for item in value)
    raise TypeError(f"expected a list or tuple, got {type(value).__name__}")


def _plan_attempt_ref(value: Mapping[str, Any]) -> ProjectPlanAttemptRef:
    return ProjectPlanAttemptRef(
        attempt_id=str(value.get("attempt_id", "")),
        status=cast(Any, value.get("status", "incomplete")),
        activated=value.get("activated") is True,
        renderability_mode=cast(Any, value.get("renderability_mode", "strict")),
        created_at=str(value.get("created_at", "")),
        parent_attempt_id=(
            value.get("parent_attempt_id")
            if isinstance(value.get("parent_attempt_id"), str)
            else None
        ),
        scope_count=(
            value.get("scope_count")
            if isinstance(value.get("scope_count"), int)
            else len(value.get("scopes", []))
            if isinstance(value.get("scopes"), list)
            else 0
        ),
        issue_count=(value.get("issue_count") if isinstance(value.get("issue_count"), int) else 0),
        repair_count=(
            value.get("repair_count")
            if isinstance(value.get("repair_count"), int)
            else sum(
                item.get("repair_count", 0)
                for item in value.get("scopes", [])
                if isinstance(item, Mapping) and isinstance(item.get("repair_count", 0), int)
            )
            if isinstance(value.get("scopes"), list)
            else 0
        ),
    )


def _plan_attempt_scope(value: Mapping[str, Any]) -> ProjectPlanAttemptScope:
    return ProjectPlanAttemptScope(
        scope_id=str(value.get("scope_id", "")),
        status=cast(Any, value.get("status", "failed")),
        source_path=value.get("source_path") if isinstance(value.get("source_path"), str) else None,
        source_sha256=(
            value.get("source_sha256") if isinstance(value.get("source_sha256"), str) else None
        ),
        semantic_planner_fingerprint=(
            value.get("semantic_planner_fingerprint")
            if isinstance(value.get("semantic_planner_fingerprint"), str)
            else None
        ),
        candidate_path=(
            value.get("candidate_path") if isinstance(value.get("candidate_path"), str) else None
        ),
        issue_count=value.get("issue_count", 0) if isinstance(value.get("issue_count"), int) else 0,
        repair_count=value.get("repair_count", 0)
        if isinstance(value.get("repair_count"), int)
        else 0,
        reused=value.get("reused") is True,
    )


def _plan_issue(value: Mapping[str, Any]) -> ProjectPlanIssue:
    fields = {
        "scope_id",
        "code",
        "reason",
        "segment_id",
        "unit_id",
        "text",
        "source_path",
        "line",
        "column",
        "end_line",
        "end_column",
        "source_context",
        "repair_safe",
        "repair_action",
        "repair_blockers",
    }
    context = value.get("source_context", ())
    blockers = value.get("repair_blockers", ())
    details = {key: item for key, item in value.items() if key not in fields}
    return ProjectPlanIssue(
        scope_id=str(value.get("scope_id", "")),
        code=str(value.get("code", "")),
        reason=str(value.get("reason", "")),
        segment_id=value.get("segment_id") if isinstance(value.get("segment_id"), str) else None,
        unit_id=value.get("unit_id") if isinstance(value.get("unit_id"), str) else None,
        text=value.get("text") if isinstance(value.get("text"), str) else None,
        source_path=value.get("source_path") if isinstance(value.get("source_path"), str) else None,
        line=value.get("line") if isinstance(value.get("line"), int) else None,
        column=value.get("column") if isinstance(value.get("column"), int) else None,
        end_line=value.get("end_line") if isinstance(value.get("end_line"), int) else None,
        end_column=value.get("end_column") if isinstance(value.get("end_column"), int) else None,
        source_context=tuple(str(item) for item in context)
        if isinstance(context, (list, tuple))
        else (),
        repair_safe=value.get("repair_safe")
        if isinstance(value.get("repair_safe"), bool)
        else None,
        repair_action=(
            value.get("repair_action") if isinstance(value.get("repair_action"), str) else None
        ),
        repair_blockers=tuple(str(item) for item in blockers)
        if isinstance(blockers, (list, tuple))
        else (),
        details=details,
    )


def _plan_inspection(value: Mapping[str, Any]) -> ProjectPlanInspection:
    attempt_raw = value.get("attempt")
    attempt = _plan_attempt_ref(attempt_raw) if isinstance(attempt_raw, Mapping) else None
    scopes_raw = value.get("scopes", [])
    issues_raw = value.get("issues", [])
    repairs_raw = value.get("repairs", [])
    segments_raw = value.get("segments", [])
    return ProjectPlanInspection(
        selected=cast(Any, value.get("selected", "attempt")),
        attempt=attempt,
        scopes=tuple(_plan_attempt_scope(row) for row in scopes_raw if isinstance(row, Mapping))
        if isinstance(scopes_raw, list)
        else (),
        issues=tuple(_plan_issue(row) for row in issues_raw if isinstance(row, Mapping))
        if isinstance(issues_raw, list)
        else (),
        repairs=tuple(_plan_issue(row) for row in repairs_raw if isinstance(row, Mapping))
        if isinstance(repairs_raw, list)
        else (),
        segments=tuple(row for row in segments_raw if isinstance(row, Mapping))
        if isinstance(segments_raw, list)
        else (),
        active_plan_status=str(value.get("active_plan_status", "missing")),
    )


def _plan_repair_result(value: Mapping[str, Any]) -> ProjectPlanRepairResult:
    attempt_raw = value.get("attempt")
    if not isinstance(attempt_raw, Mapping):
        raise TypeError("planning repair result is missing its attempt manifest")
    issues_raw = value.get("issues", [])
    return ProjectPlanRepairResult(
        attempt=_plan_attempt_ref(attempt_raw),
        activated=value.get("activated") is True,
        repairs=value.get("repairs", 0) if isinstance(value.get("repairs"), int) else 0,
        reused_scopes=tuple(str(item) for item in value.get("reused_scopes", [])),
        rebuilt_scopes=tuple(str(item) for item in value.get("rebuilt_scopes", [])),
        issues=tuple(_plan_issue(row) for row in issues_raw if isinstance(row, Mapping))
        if isinstance(issues_raw, list)
        else (),
        dry_run=value.get("dry_run") is True,
        source_files_changed=value.get("source_files_changed") is True,
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

    def settings(self, project: ProjectLike) -> ProjectSettings:
        """Return the project's detached immutable desired pipeline settings."""
        internal = self._load(project)
        return self._call(
            lambda: project_settings_from_manifest(internal.manifest, internal.state_root)
        )

    def configure(
        self,
        project: ProjectLike,
        settings: ProjectSettings,
        *,
        validate: bool = True,
    ) -> ProjectSettings:
        """Atomically replace supported pipeline settings for a project."""
        internal = self._load(project)
        return self._call(
            lambda: self._write_settings(
                internal,
                lambda manifest: with_project_settings(manifest, settings, internal.state_root),
                validate=validate,
                operation="project-configure",
            )
        )

    def update_settings(
        self,
        project: ProjectLike,
        patch: ProjectSettingsPatch,
        *,
        validate: bool = True,
    ) -> ProjectSettings:
        """Atomically patch project settings, preserving sections marked UNSET."""
        internal = self._load(project)
        return self._call(
            lambda: self._write_settings(
                internal,
                lambda manifest: apply_project_settings_patch(manifest, patch, internal.state_root),
                validate=validate,
                operation="project-settings-update",
            )
        )

    def _write_settings(
        self,
        project: project_internal.Project,
        transform: Callable[[ProjectManifest], ProjectManifest],
        *,
        validate: bool,
        operation: str,
    ) -> ProjectSettings:
        def update(manifest: ProjectManifest) -> ProjectManifest:
            candidate = transform(manifest)
            if validate:
                settings = project_settings_from_manifest(candidate, project.state_root)
                settings = self._materialize_project_synthesis(project, settings)
                candidate = with_project_settings(manifest, settings, project.state_root)
            return candidate

        updated = project_internal.update_project_manifest(project, update, operation=operation)
        return project_settings_from_manifest(updated.manifest, updated.state_root)

    def _materialize_project_synthesis(
        self, project: project_internal.Project, settings: ProjectSettings
    ) -> ProjectSettings:
        synthesis = settings.synthesis
        if synthesis is None:
            return settings
        resolution = self._resolve_synthesis_internal(
            project,
            project_synthesis_request(synthesis),
            merge_saved_settings=False,
        )
        values: dict[str, Any] = {
            "language": resolution.language,
            "engine": resolution.engine,
            "model": resolution.model,
            "model_source": resolution.model_source,
            "quality": resolution.quality,
            "voice": resolution.voice,
            "speed": resolution.speed,
            "spacy": resolution.spacy,
            "short_sentence": resolution.short_sentence,
            "g2p_fallback": resolution.g2p_fallback,
            "lexicon_data_policy": resolution.lexicon_data_policy,
            "language_detection": resolution.language_detection,
            "detect_languages": resolution.detect_languages,
            "allow_experimental": resolution.allow_experimental,
            "voice_level": resolution.voice_level,
            "pause_mode": resolution.pause_mode,
            "unit": resolution.unit,
        }
        if (
            synthesis.lexicons is None
            and synthesis.clear_lexicons is not True
            and synthesis.auto_lexicons is not True
        ):
            if resolution.lexicons is None:
                values["auto_lexicons"] = True
            else:
                values["lexicons"] = resolution.lexicons
        return replace(settings, synthesis=replace(synthesis, **values))

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
            NextAction(
                stage=cast(StageName, row["stage"]),
                reason=str(row["reason"]),
                command=str(row["command"]) if row.get("command") else None,
            )
            for row in raw["next_actions"]
        )
        attempt_raw = raw.get("planning_attempt")
        planning_attempt = (
            _plan_attempt_ref(attempt_raw) if isinstance(attempt_raw, Mapping) else None
        )
        return ProjectStatus(self._ref(internal), stages, issues, actions, planning_attempt)

    def plan(
        self,
        project: ProjectLike,
        *,
        options: ProjectPlanOptions | None = None,
        on_event: EventHandler | None = None,
    ) -> ProjectPlanResult:
        internal = self._load(project)
        handler = self._handler(on_event)
        operation = "projects.plan"
        self._notify(handler, ReadioEvent(kind="operation.started", operation=operation))
        self._notify(handler, ReadioEvent(kind="stage.started", operation=operation, stage="plan"))
        planning_handler = self._planning_handler(handler, operation)
        options = options or ProjectPlanOptions()
        result = self._call(
            lambda: plan_project(
                internal,
                self._app.config,
                on_progress=planning_handler,
                renderability_mode=options.renderability,
            )
        )
        self._notify(
            handler,
            ReadioEvent(
                kind="stage.completed",
                operation=operation,
                stage="plan",
                details={
                    "scope_count": len(result.scopes),
                    "renderability_mode": result.renderability_mode,
                    "renderability_guaranteed": result.renderability_guaranteed,
                    "repairs": result.repairs,
                    "attempt_id": result.attempt_id,
                    "activated": result.activated,
                    "reused_scopes": list(result.reused_scopes),
                    "rebuilt_scopes": list(result.rebuilt_scopes),
                },
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
        diagnostics = tuple(
            Diagnostic(
                code=str(row.get("code", "")),
                severity=(
                    "error"
                    if row.get("severity") == "error"
                    else "warning"
                    if row.get("severity") == "warning"
                    else "info"
                ),
                message=str(row.get("message", "")),
                source_path=(
                    Path(source_path)
                    if isinstance((source_path := row.get("source_path")), str)
                    else None
                ),
                line=line if isinstance((line := row.get("line")), int) else None,
                details={
                    key: value
                    for key, value in row.items()
                    if key not in {"code", "severity", "message", "source_path", "line"}
                },
            )
            for row in result.diagnostics
        )
        return ProjectPlanResult(
            project=self._ref(internal),
            scopes=scopes,
            renderability_mode=result.renderability_mode,
            renderability_guaranteed=result.renderability_guaranteed,
            repairs=result.repairs,
            diagnostics=diagnostics,
            attempt_id=result.attempt_id,
            activated=result.activated,
            reused_scopes=result.reused_scopes,
            rebuilt_scopes=result.rebuilt_scopes,
        )

    def inspect_plan(
        self,
        project: ProjectLike,
        *,
        options: ProjectPlanInspectionOptions | None = None,
    ) -> ProjectPlanInspection:
        """Inspect active and persisted planning state without mutating the project."""
        from ..stages.planning import inspect_plan_state

        internal = self._load(project)
        selected = options or ProjectPlanInspectionOptions()
        raw = self._call(
            lambda: inspect_plan_state(
                internal,
                attempt=selected.attempt,
                scope_id=selected.scope_id,
                issues=selected.issues,
                repairs=selected.repairs,
                segment_id=selected.segment_id,
                unit_id=selected.unit_id,
                source_context=selected.source_context,
            )
        )
        if not isinstance(raw, Mapping):
            raise TypeError("plan inspection returned an invalid result")
        return _plan_inspection(raw)

    def repair_plan(
        self,
        project: ProjectLike,
        *,
        options: ProjectPlanRepairOptions | None = None,
        on_event: EventHandler | None = None,
    ) -> ProjectPlanRepairResult:
        """Retry a blocked attempt using only UtterPlan-approved safe repairs."""
        from ..stages.planning import repair_plan_project

        internal = self._load(project)
        selected = options or ProjectPlanRepairOptions()
        handler = self._handler(on_event)
        operation = "projects.repair_plan"
        self._notify(handler, ReadioEvent(kind="operation.started", operation=operation))
        self._notify(handler, ReadioEvent(kind="stage.started", operation=operation, stage="plan"))
        raw = self._call(
            lambda: repair_plan_project(
                internal,
                self._app.config,
                attempt_id=selected.attempt_id,
                scope_id=selected.scope_id,
                dry_run=selected.dry_run,
                on_progress=self._planning_handler(handler, operation),
            )
        )
        if not isinstance(raw, Mapping):
            raise TypeError("plan repair returned an invalid result")
        result = _plan_repair_result(raw)
        self._notify(
            handler,
            ReadioEvent(
                kind="stage.completed",
                operation=operation,
                stage="plan",
                details={
                    "attempt_id": result.attempt.attempt_id,
                    "activated": result.activated,
                    "repairs": result.repairs,
                    "reused_scopes": list(result.reused_scopes),
                    "rebuilt_scopes": list(result.rebuilt_scopes),
                    "dry_run": result.dry_run,
                },
            ),
        )
        self._notify(handler, ReadioEvent(kind="operation.completed", operation=operation))
        return result

    def resolve_synthesis(
        self,
        project: ProjectLike,
        request: SynthesisRequest | None = None,
        *,
        voice_bindings: Mapping[str, str] | None = None,
        use_saved_settings: bool = True,
    ) -> SynthesisResolution:
        """Resolve project synthesis without rendering or mutation.

        Set ``use_saved_settings=False`` to resolve a fresh candidate for a
        configuration editor or guided setup flow, ignoring persisted project
        synthesis preferences while retaining normal Readio defaults.
        """
        internal = self._load(project)
        request = request or SynthesisRequest()
        return self._resolve_synthesis_internal(
            internal,
            request,
            voice_bindings=voice_bindings,
            merge_saved_settings=use_saved_settings,
        )

    def _resolve_synthesis_internal(
        self,
        internal: project_internal.Project,
        request: SynthesisRequest,
        *,
        voice_bindings: Mapping[str, str] | None = None,
        merge_saved_settings: bool = True,
    ) -> SynthesisResolution:
        project_request = _project_request(
            internal,
            self._app.config,
            request,
            voice_bindings=voice_bindings,
            use_saved_settings=merge_saved_settings,
        )
        _effective_request, resolved, _adapter, _profile = self._call(
            lambda: resolve_project_synthesis(
                internal,
                self._app.config,
                project_request,
                merge_saved_settings=False,
            )
        )
        selection = resolved.selection
        if selection is None:
            raise ValueError("project synthesis has no effective engine selection")
        options = selection.options
        diagnostics = tuple(
            Diagnostic.from_plan(item)
            for item in resolved.plan.diagnostics
            if item.severity != "error"
        )
        return SynthesisResolution(
            engine=selection.engine,
            language=selection.language,
            voice=selection.voice,
            model=str(selection.metadata.get("model") or selection.target_id),
            model_source=cast(str | None, options.get("model_source")),
            quality=cast(str | None, options.get("quality")),
            speed=float(options.get("speed", self._app.config.reader.speed)),
            unit=resolved.plan.planning.unit,
            pause_mode=resolved.plan.planning.pause_mode,
            voice_level=cast(str | None, options.get("voice_level")),
            spacy=resolved.plan.planning.spacy,
            short_sentence=cast(str | None, options.get("short_sentence")),
            lexicons=_optional_string_tuple(options.get("lexicons")),
            g2p_fallback=cast(str | None, options.get("g2p_fallback")),
            lexicon_data_policy=cast(str | None, options.get("lexicon_data_policy")),
            language_detection=cast(str | None, options.get("language_detection")),
            detect_languages=_optional_string_tuple(options.get("detect_languages")),
            allow_experimental=bool(options.get("allow_experimental", False)),
            diagnostics=diagnostics,
        )

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
            artifacts=tuple(
                ProjectSynthesisArtifact(
                    scope_id=artifact.scope_id,
                    segment_id=artifact.segment_id,
                    text=artifact.text or "",
                    audio_path=artifact.path,
                    sample_rate=artifact.sample_rate,
                    frames=artifact.frames,
                    audio_sha256=artifact.audio_sha256,
                    speech_hash=artifact.speech_hash,
                    synthesis_key=artifact.synthesis_key,
                    profile_id=artifact.profile_id,
                    lowering_sha256=artifact.lowering_sha256,
                    word_timings=tuple(
                        cast(Mapping[str, JsonValue], json_value(item))
                        for item in artifact.word_timings
                    ),
                )
                for artifact in raw["artifacts"]
            ),
        )

    def compose(
        self,
        project: ProjectLike,
        options: CompositionOptions | None = None,
        *,
        on_event: EventHandler | None = None,
    ) -> ProjectCompositionResult:
        internal = self._load(project)
        settings = project_settings_from_manifest(internal.manifest, internal.state_root)
        options = options or settings.composition or CompositionOptions()
        handler = self._handler(on_event)
        operation = "projects.compose"
        self._notify(handler, ReadioEvent(kind="operation.started", operation=operation))
        self._notify(
            handler, ReadioEvent(kind="stage.started", operation=operation, stage="composition")
        )
        raw = self._call(
            lambda: compose_project(
                internal,
                mastering=options.mastering,
                target_lufs=options.target_lufs,
                true_peak_ceiling_dbtp=options.true_peak_ceiling_dbtp,
                peak_policy=options.peak_policy,
                clip_policy=options.clip_policy,
                output_sample_rate=options.sample_rate,
                on_progress=self._composition_handler(handler, operation),
                on_phase=self._phase_handler(handler, operation),
            )
        )
        loudness_value = raw.get("loudness")
        loudness = (
            LoudnessSummary.from_mapping(loudness_value)
            if isinstance(loudness_value, Mapping)
            else None
        )
        self._notify(
            handler,
            ReadioEvent(
                kind="stage.completed",
                operation=operation,
                stage="composition",
                sample_count=raw["frames"],
                sample_rate=raw["sample_rate"],
                details={
                    "items": raw["items"],
                    "loudness": loudness.to_dict() if loudness is not None else None,
                },
            ),
        )
        self._notify(handler, ReadioEvent(kind="operation.completed", operation=operation))
        return ProjectCompositionResult(
            project=self._ref(internal),
            composition_id=str(raw["composition_id"]),
            frames=int(raw["frames"]),
            items=int(raw["items"]),
            master_path=Path(raw["master"]),
            loudness=loudness,
        )

    def export(
        self,
        project: ProjectLike,
        options: ExportOptions | None = None,
        *,
        on_event: EventHandler | None = None,
    ) -> ProjectExportResult:
        internal = self._load(project)
        settings = project_settings_from_manifest(internal.manifest, internal.state_root)
        options = options or settings.export or ExportOptions()
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
                force=options.force,
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
            export_id=str(raw["export_id"]),
        )

    def build(
        self,
        project: ProjectLike,
        request: ProjectBuildRequest | None = None,
        *,
        on_event: EventHandler | None = None,
    ) -> ProjectBuildResult:
        internal = self._load(project)
        handler = self._handler(on_event)
        operation = "projects.build"
        self._notify(handler, ReadioEvent(kind="operation.started", operation=operation))
        raw = self._call(
            lambda: build_project(
                internal,
                self._app.config,
                request,
                on_synthesis_event=self._synthesis_handler(handler, operation),
                on_planning_progress=self._planning_handler(handler, operation),
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

        def composition_event(event: CompositionProgress) -> None:
            nonlocal composition_started, composition_completed
            kind = event.kind
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
        loudness_value = raw.get("loudness")
        loudness = (
            LoudnessSummary.from_mapping(loudness_value)
            if isinstance(loudness_value, Mapping)
            else None
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
            loudness=loudness,
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

    def _planning_handler(
        self,
        handler: EventHandler | None,
        operation: str,
    ) -> PlanningProgressCallback | None:
        if handler is None:
            return None

        def forward(progress: ProjectPlanningProgress) -> None:
            if progress.kind == "scope.started":
                scope_index = progress.scope_index or 1
                details: dict[str, JsonValue] = {"scope_index": scope_index}
                event = ReadioEvent(
                    kind="progress",
                    operation=operation,
                    stage="plan",
                    progress_kind="item.started",
                    scope_id=progress.scope_id,
                    completed=scope_index - 1,
                    total=progress.scope_total,
                    details=details,
                )
            elif progress.kind == "scope.completed":
                scope_index = progress.scope_index or 1
                details = {"scope_index": scope_index}
                event = ReadioEvent(
                    kind="progress",
                    operation=operation,
                    stage="plan",
                    progress_kind="item.completed",
                    scope_id=progress.scope_id,
                    completed=scope_index,
                    total=progress.scope_total,
                    details=details,
                )
            elif progress.kind in {
                "renderability.started",
                "renderability.completed",
                "renderability.failed",
            }:
                phase_status = progress.kind.split(".", maxsplit=1)[1]
                message = {
                    "started": "Checking semantic renderability",
                    "completed": "Semantic renderability checked",
                    "failed": "Semantic renderability failed",
                }[phase_status]
                details: dict[str, JsonValue] = {
                    "phase": "renderability_preflight",
                    "event_kind": f"phase.{phase_status}",
                }
                details.update(progress.details or {})
                event = ReadioEvent(
                    kind="progress",
                    operation=operation,
                    stage="plan",
                    progress_kind="phase",
                    message=message,
                    scope_id=progress.scope_id,
                    details=details,
                )
            elif progress.planner_event is not None:
                planner_event = progress.planner_event
                details = {
                    "phase": planner_event.phase,
                    "event_kind": planner_event.kind,
                }
                for key, value in (
                    ("pass_index", planner_event.pass_index),
                    ("pass_total", planner_event.pass_total),
                    ("language", planner_event.language),
                    ("provider", planner_event.provider),
                    ("model", planner_event.model),
                    ("char_count", planner_event.char_count),
                    ("run_completed", planner_event.completed),
                    ("run_total", planner_event.total),
                ):
                    if value is not None:
                        details[key] = cast(JsonValue, json_value(value))
                for key in ("reused", "skipped", "reason"):
                    value = planner_event.details.get(key)
                    if value is not None and isinstance(value, (str, int, float, bool)):
                        details[key] = cast(JsonValue, json_value(value))
                event = ReadioEvent(
                    kind="progress",
                    operation=operation,
                    stage="plan",
                    progress_kind="phase",
                    message=self._planning_message(planner_event),
                    scope_id=progress.scope_id,
                    details=details,
                )
            else:
                return
            self._notify(handler, event)

        return forward

    def _planning_message(self, event: Any) -> str:
        if event.kind == "model.started":
            provider = event.provider
            label = "spaCy" if provider == "spacy" else provider
            return f"Loading {label} model" if label else "Loading linguistic model"
        if event.kind == "model.completed":
            provider = "spaCy" if event.provider == "spacy" else event.provider
            return f"{provider} model ready" if provider else "Linguistic model ready"
        if event.phase == "parse":
            return "Parsing document"
        if event.phase == "source_analysis":
            return "Linguistic analysis"
        if event.phase == "preparation":
            return "Preparing spoken text"
        if event.phase == "spoken_analysis":
            return (
                "Reusing linguistic analysis"
                if event.details.get("reused") is True
                else "Linguistic analysis"
            )
        if event.phase == "segmentation":
            return "Segmenting speech units"
        if event.phase == "finalization":
            return "Finalizing semantic plan"
        return "Planning"

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
        internal_kind = getattr(event, "kind", None)
        progress_kinds = {
            "unit_started": "unit.started",
            "unit_finished": "unit.completed",
            "segment_started": "segment.started",
            "segment_finished": "segment.completed",
            "preflight_segment_checked": "item.completed",
        }
        progress_kind = cast(ProgressKind, progress_kinds.get(internal_kind, "phase"))
        phase_messages = {
            "profile_resolved": "Synthesis plan ready",
            "cache_scanned": "Synthesis cache scanned",
            "engine_open_started": "Loading synthesis model",
            "engine_open_finished": "Synthesis model ready",
            "prepare_started": "Preparing synthesis",
            "prepare_finished": "Synthesis prepared",
            "activation_started": "Activating synthesis",
            "activation_finished": "Synthesis activated",
            "preflight_started": "Preflighting synthesis targets",
            "preflight_measurement_started": "Checking known target request limits",
            "preflight_completed": "Synthesis preflight complete",
            "preflight_failed": "Synthesis preflight failed",
        }
        details = getattr(event, "details", {}) or {}
        safe = self._safe_details(
            details,
            _STATUS_DETAIL_KEYS
            | {
                "engine",
                "engine_version",
                "provider",
                "routing_mode",
                "source",
                "source_format",
                "selected_units",
                "targets",
                "voice_bindings",
                "segment_ids",
                "scope_kind",
                "scope_title",
                "scope_number",
                "scope_index",
                "scope_total",
                "scope_completed",
                "scope_render_total",
                "global_completed",
                "global_total",
                "phase",
                "issues",
                "rendered_new_segments",
                "synthesis_requests",
                "audio_artifacts_written",
                "reason",
                "field",
                "source_start",
                "source_end",
                "request_text",
                "actual",
                "maximum",
                "limit_unit",
                "measurement_source",
            },
        )
        text = getattr(event, "text", None)
        if text is not None:
            safe = {**safe, "text": json_value(text)}
        self._notify(
            handler,
            ReadioEvent(
                kind="progress",
                operation=operation,
                stage="synthesis",
                progress_kind=progress_kind,
                message=phase_messages.get(internal_kind),
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

        state = {"completed": 0, "total": 0}

        def forward(event: CompositionProgress) -> None:
            internal_kind = event.kind
            phase_key = {
                "assembly_started": "assembly",
                "loudness_started": "loudness",
            }.get(internal_kind)
            if phase_key is not None:
                state.setdefault("phase_started", {})[phase_key] = time.perf_counter()
            phase_ended = {
                "assembly_completed": "assembly",
                "loudness_completed": "loudness",
            }.get(internal_kind)
            details = {key: value for key, value in event.details.items() if key != "item_metadata"}
            if phase_ended is not None:
                started = state.setdefault("phase_started", {}).pop(phase_ended, None)
                if started is not None:
                    details["phase_duration_seconds"] = max(0.0, time.perf_counter() - started)
            if internal_kind == "compose_completed":
                return
            if internal_kind == "compose_started":
                details = dict(event.details)
                metadata_kinds = details.get("metadata_kinds", {})
                speech_count = (
                    metadata_kinds.get("speech") if isinstance(metadata_kinds, Mapping) else None
                )
                total = (
                    speech_count if isinstance(speech_count, int) else details.get("clip_items", 0)
                )
                state["total"] = total if isinstance(total, int) else 0
                self._forward_composition_event(
                    handler,
                    operation,
                    event,
                    completed=0,
                    total=state["total"],
                )
                return
            item_kind = event.item_kind
            if internal_kind == "item_completed" and item_kind == "clip":
                metadata = event.details.get("item_metadata", {}) or {}
                if metadata.get("kind") != "silence":
                    state["completed"] += 1
            is_clip = item_kind == "clip"
            self._forward_composition_event(
                handler,
                operation,
                event,
                completed=state["completed"] if is_clip else None,
                total=state["total"] if is_clip else None,
                details=cast(Mapping[str, JsonValue], details),
            )

        return forward

    def _forward_composition_event(
        self,
        handler: EventHandler,
        operation: str,
        event: CompositionProgress,
        *,
        completed: int | None = None,
        total: int | None = None,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        internal_kind = event.kind
        event_details = details or {}
        if internal_kind == "compose_completed":
            return
        if internal_kind == "item_started":
            item_kind = event.item_kind
            progress_kind = "segment.started" if item_kind == "clip" else "item.started"
            message = "Preparing segment" if item_kind == "clip" else "Preparing item"
        elif internal_kind == "item_completed":
            item_kind = event.item_kind
            progress_kind = "segment.completed" if item_kind == "clip" else "item.completed"
            message = "Segment complete" if item_kind == "clip" else "Item complete"
        elif internal_kind == "compose_started":
            progress_kind = "phase"
            message = None
        elif internal_kind == "assembly_started":
            progress_kind = "phase"
            message = "Assembling master audio"
        elif internal_kind == "assembly_completed":
            progress_kind = "phase"
            duration = event_details.get("phase_duration_seconds")
            message = (
                f"Audio assembly complete in {duration:.3f}s"
                if isinstance(duration, (int, float))
                else "Audio assembly complete"
            )
        elif internal_kind == "loudness_started":
            progress_kind = "phase"
            message = "Measuring loudness and true peak"
        elif internal_kind == "loudness_completed":
            progress_kind = "phase"
            timings = [
                ("analysis", event_details.get("analysis_seconds")),
                ("gain", event_details.get("gain_seconds")),
                ("post-gain metrics", event_details.get("post_gain_metrics_seconds")),
            ]
            timing_text = "; ".join(
                f"{name} {value:.3f}s" for name, value in timings if isinstance(value, (int, float))
            )
            duration = event_details.get("phase_duration_seconds")
            if isinstance(duration, (int, float)):
                timing_text = f"total {duration:.3f}s" + (f"; {timing_text}" if timing_text else "")
            message = (
                f"Loudness finalization complete ({timing_text})"
                if timing_text
                else "Loudness finalization complete"
            )
        else:
            progress_kind = "phase"
            message = {
                "source_load_started": "Loading audio source",
                "operation_started": "Applying audio operation",
                "resample_started": "Resampling audio",
            }.get(internal_kind, "Composing audio")
        item_metadata = event.details.get("item_metadata", {}) or {}
        segment_id = item_metadata.get("segment_id") or event.item_id
        self._notify(
            handler,
            ReadioEvent(
                kind="progress",
                operation=operation,
                stage="composition",
                progress_kind=cast(ProgressKind, progress_kind),
                message=message,
                completed=completed,
                total=total,
                sample_count=getattr(event, "output_frames", None),
                sample_rate=getattr(event, "target_sample_rate", None),
                audio_seconds=getattr(event, "completed_audio_seconds", None),
                total_audio_seconds=getattr(event, "total_audio_seconds", None),
                segment_id=segment_id,
                details=event_details,
            ),
        )

    def _phase_handler(self, handler: EventHandler | None, operation: str):
        if handler is None:
            return None

        def phase(message: str) -> None:
            self._notify(
                handler,
                ReadioEvent(
                    kind="progress",
                    operation=operation,
                    stage="composition",
                    progress_kind="phase",
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
                ReadioEvent(
                    kind="stage.started",
                    operation=operation,
                    stage=cast(EventStage, stage),
                ),
            )
        else:
            self._notify(
                handler,
                ReadioEvent(
                    kind="stage.completed",
                    operation=operation,
                    stage=cast(EventStage, stage),
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
