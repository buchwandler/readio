"""Engine-free semantic planning stage."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from utterplan import PlannerProgressEvent

from ..audiobook import refresh_audiobook_index
from ..document import InputDocument, document_from_text
from ..errors import SSMDInputError
from ..planning import (
    SUPPORTED_UTTERPLAN_SCHEMA_VERSION,
    CompiledSemanticPlan,
    PlanningPolicy,
    PlanSchemaMismatchError,
    UtterancePlan,
    compile_semantic_plan,
    load_utterplan_v3,
    serialize_utterplan,
)
from ..project import (
    Project,
    atomic_write_bytes,
    atomic_write_json,
    hash_file,
    project_lock,
    sha256_bytes,
)
from ..project_model import DocumentScope, PlanIndex, PlanScope
from ..project_settings import (
    project_planning_config,
    project_planning_settings_fingerprint,
    project_settings_from_manifest,
)
from ..reader import prepare_input_document
from ..ssmd import language_detection_hint_from_header, parse_ssmd_09


@dataclass(frozen=True, slots=True)
class ResolvedSemanticPlanning:
    document: InputDocument
    policy: PlanningPolicy
    compiled: CompiledSemanticPlan


@dataclass(frozen=True, slots=True)
class PlannedScope:
    scope: PlanScope
    compiled: CompiledSemanticPlan


@dataclass(frozen=True, slots=True)
class ProjectPlanningResult:
    scopes: tuple[PlannedScope, ...]


@dataclass(frozen=True, slots=True)
class ProjectPlanningProgress:
    """Internal planning progress enriched with project-scope context."""

    kind: Literal["scope.started", "scope.completed", "planner"]
    scope_id: str | None = None
    scope_index: int | None = None
    scope_total: int | None = None
    planner_event: PlannerProgressEvent | None = None


PlannerProgressCallback = Callable[[PlannerProgressEvent], None]
PlanningProgressCallback = Callable[[ProjectPlanningProgress], None]


def resolve_semantic_planning(
    cfg: Any,
    document: InputDocument,
    *,
    on_progress: PlannerProgressCallback | None = None,
) -> ResolvedSemanticPlanning:
    prepared = prepare_input_document(document)
    planner_document_format = "ssmd" if prepared.format == "ssmd" else "plain"
    policy = PlanningPolicy.from_semantic_config(cfg, document_format=planner_document_format)
    if prepared.format == "ssmd":
        metadata_language = (
            prepared.provenance.metadata.get("language")
            if prepared.provenance is not None
            else None
        )
        if isinstance(metadata_language, str) and metadata_language.strip():
            policy = replace(policy, language=metadata_language.strip())

        parsed = parse_ssmd_09(prepared.text, source_path=prepared.source_path)
        header_language = parsed.header.get("language")
        if isinstance(header_language, str) and header_language.strip():
            policy = replace(policy, language=header_language.strip())
        detection = language_detection_hint_from_header(
            parsed.header,
            source_path=prepared.source_path,
        )
        if detection is not None:
            policy = replace(
                policy,
                language_detection=detection[0],
                detect_languages=detection[1],
            )

    compiled = compile_semantic_plan(prepared, planning=policy, on_progress=on_progress)
    return ResolvedSemanticPlanning(prepared, policy, compiled)


def _write_plan_artifact(path: Path, compiled: CompiledSemanticPlan) -> str:
    serialized = compiled.serialized or serialize_utterplan(compiled.plan)
    atomic_write_bytes(path, serialized)
    return sha256_bytes(serialized)


def prepare_project_document(project: Project) -> InputDocument:
    """Legacy schema-v1 source normalization only."""
    paths = project.paths
    source = paths["source"]
    raw = source.read_text(encoding="utf-8")
    metadata = __import__("json").loads(paths["document_metadata"].read_text(encoding="utf-8"))
    input_format = metadata.get("input_format", project.manifest.source_format)
    if input_format == "markdown":
        normalized_document = document_from_text(raw, source_path=source, input_format="markdown")
        normalized = normalized_document.text
        document_format = normalized_document.format
    elif input_format == "ssmd":
        normalized = raw
        document_format = "ssmd"
    else:
        normalized = raw
        document_format = "text"
    atomic_write_bytes(paths["document_text"], normalized.encode("utf-8"))
    atomic_write_json(
        paths["document_metadata"],
        {
            "format": "readio.document",
            "schema_version": 1,
            "source_sha256": hash_file(source),
            "document_sha256": sha256_bytes(normalized.encode("utf-8")),
            "input_format": input_format,
            "document_format": document_format,
            "source_path": f"../{project.manifest.source_path}",
        },
    )
    return InputDocument(text=normalized, source_path=source, format=document_format)


def compile_project_scope(
    project: Project,
    cfg: Any,
    scope: DocumentScope,
    document: InputDocument,
    *,
    document_sha256: str | None = None,
    on_progress: PlanningProgressCallback | None = None,
    scope_index: int = 1,
    scope_total: int = 1,
) -> PlannedScope:
    """Compile one document scope without writing artifacts or mutating indexes."""
    if not scope.id or "/" in scope.id or "\\" in scope.id or scope.id in {".", ".."}:
        raise ValueError("scope_id must be a simple identifier")
    synthesis_settings = project_settings_from_manifest(
        project.manifest, project.state_root
    ).synthesis
    planning_config = project_planning_config(cfg, synthesis_settings)
    planner_callback: PlannerProgressCallback | None = None
    if on_progress is not None:

        def planner_progress(event: PlannerProgressEvent) -> None:
            on_progress(
                ProjectPlanningProgress(
                    kind="planner",
                    scope_id=scope.id,
                    scope_index=scope_index,
                    scope_total=scope_total,
                    planner_event=event,
                )
            )

        planner_callback = planner_progress
    resolved = resolve_semantic_planning(planning_config, document, on_progress=planner_callback)
    relative = (
        Path("document.utterplan.json")
        if scope.id == "document" and scope.kind == "document"
        else Path("chapters") / f"{scope.id}.utterplan.json"
    )
    serialized = resolved.compiled.serialized or resolved.compiled.plan.to_json().encode("utf-8")
    input_path = (
        project.workspace_path(scope.path)
        if project.manifest.schema_version == 4
        else project.path(scope.path)
    )
    document_sha = document_sha256
    if document_sha is None:
        document_sha = (
            hash_file(input_path)
            if input_path.is_file()
            else sha256_bytes(document.text.encode("utf-8"))
        )
    plan_scope = PlanScope(
        id=scope.id,
        kind=scope.kind,
        path=relative.as_posix(),
        title=scope.title,
        plan_id=resolved.compiled.plan_id,
        sha256=sha256_bytes(serialized),
        document_sha256=document_sha,
    )
    return PlannedScope(scope=plan_scope, compiled=resolved.compiled)


def plan_project_scope(
    project: Project,
    cfg: Any,
    scope_id: str,
    document: InputDocument,
    *,
    kind: str = "chapter",
    title: str | None = None,
    document_sha256: str | None = None,
    on_progress: PlanningProgressCallback | None = None,
) -> CompiledSemanticPlan:
    """Compile and replace one independent plan scope while preserving others."""
    if project.manifest.schema_version == 4:
        refresh_audiobook_index(project)
    scope = DocumentScope(
        id=scope_id,
        kind=kind,
        path="",
        input_format=document.format,
        title=title,
    )
    with project_lock(project, operation="plan-scope"):
        if on_progress is not None:
            on_progress(
                ProjectPlanningProgress(
                    kind="scope.started",
                    scope_id=scope.id,
                    scope_index=1,
                    scope_total=1,
                )
            )
        planned = compile_project_scope(
            project,
            cfg,
            scope,
            document,
            document_sha256=document_sha256,
            on_progress=on_progress,
            scope_index=1,
            scope_total=1,
        )
        if on_progress is not None:
            on_progress(
                ProjectPlanningProgress(
                    kind="scope.completed",
                    scope_id=scope.id,
                    scope_index=1,
                    scope_total=1,
                )
            )
        plan_path = project.state_root / "plan" / planned.scope.path
        plan_sha = _write_plan_artifact(plan_path, planned.compiled)
        replacement = PlanScope(
            id=planned.scope.id,
            kind=planned.scope.kind,
            path=planned.scope.path,
            title=planned.scope.title,
            plan_id=planned.scope.plan_id,
            sha256=plan_sha,
            document_sha256=planned.scope.document_sha256,
        )
        old_index = project.load_plan_index() if project.paths["plan_index"].is_file() else None
        old_scopes = old_index.scopes if old_index is not None else ()
        scopes = tuple(replacement if item.id == scope_id else item for item in old_scopes)
        if not any(item.id == scope_id for item in old_scopes):
            scopes = (*scopes, replacement)
        current_settings_sha256 = project_planning_settings_fingerprint(
            project_settings_from_manifest(project.manifest, project.state_root).synthesis
        )
        settings_sha256 = (
            current_settings_sha256
            if old_index is None
            or old_index.project_planning_settings_sha256 == current_settings_sha256
            else old_index.project_planning_settings_sha256
        )
        atomic_write_json(
            project.paths["plan_index"],
            PlanIndex(
                scopes=tuple(scopes),
                project_planning_settings_sha256=settings_sha256,
            ).to_dict(),
        )
        return planned.compiled


def plan_project(
    project: Project,
    cfg: Any,
    *,
    on_progress: PlanningProgressCallback | None = None,
) -> ProjectPlanningResult:
    """Plan every persisted document scope, then atomically replace the plan index."""
    if project.manifest.schema_version == 4:
        refresh_audiobook_index(project)
    with project_lock(project, operation="plan"):
        document_scopes = project.document_scopes()
        planned_scopes = []
        for index, scope in enumerate(document_scopes, start=1):
            if on_progress is not None:
                on_progress(
                    ProjectPlanningProgress(
                        kind="scope.started",
                        scope_id=scope.id,
                        scope_index=index,
                        scope_total=len(document_scopes),
                    )
                )
            if project.manifest.schema_version == 1:
                document = prepare_project_document(project)
            else:
                document = project.load_document_scope(scope)
            if on_progress is None:
                planned = compile_project_scope(project, cfg, scope, document)
            else:
                planned = compile_project_scope(
                    project,
                    cfg,
                    scope,
                    document,
                    on_progress=on_progress,
                    scope_index=index,
                    scope_total=len(document_scopes),
                )
            planned_scopes.append(planned)
            # Scope completion means semantic compilation succeeded; artifacts and the index
            # are persisted only after every scope has compiled successfully below.
            if on_progress is not None:
                on_progress(
                    ProjectPlanningProgress(
                        kind="scope.completed",
                        scope_id=scope.id,
                        scope_index=index,
                        scope_total=len(document_scopes),
                    )
                )

        for planned in planned_scopes:
            plan_path = project.state_root / "plan" / planned.scope.path
            _write_plan_artifact(plan_path, planned.compiled)
        settings_sha256 = project_planning_settings_fingerprint(
            project_settings_from_manifest(project.manifest, project.state_root).synthesis
        )
        index = PlanIndex(
            scopes=tuple(item.scope for item in planned_scopes),
            project_planning_settings_sha256=settings_sha256,
        )
        atomic_write_json(project.paths["plan_index"], index.to_dict())
        return ProjectPlanningResult(scopes=tuple(planned_scopes))


def plan_document(document: InputDocument, cfg: Any, output: Path) -> CompiledSemanticPlan:
    resolved = resolve_semantic_planning(cfg, document)
    _write_plan_artifact(output, resolved.compiled)
    return resolved.compiled


def load_scope_plan(project: Project, scope: PlanScope) -> UtterancePlan:
    return load_utterplan_v3(project.state_root / "plan" / scope.path)


def load_primary_scope_plan(project: Project) -> UtterancePlan:
    scopes = project.load_plan_index().scopes
    if len(scopes) != 1:
        raise ValueError("plan scope is required when a project has multiple scopes")
    return load_scope_plan(project, scopes[0])


def _plan_artifact_status(project: Project, document_format: str) -> dict[str, Any]:
    """Validate the indexed plans against the current semantic document."""
    try:
        index = project.load_plan_index()
    except (OSError, UnicodeError, ValueError):
        return {
            "state": "stale",
            "reason": "plan.index.invalid",
            "details": {},
        }
    current_settings_sha256 = project_planning_settings_fingerprint(
        project_settings_from_manifest(project.manifest, project.state_root).synthesis
    )
    if index.project_planning_settings_sha256 != current_settings_sha256:
        return {
            "state": "stale",
            "reason": "plan.stale.project_settings_changed",
            "details": {},
        }
    try:
        document_scope_list = project.document_scopes()
        document_scopes = {scope.id: scope for scope in document_scope_list}
    except (OSError, UnicodeError, ValueError):
        document_scope_list = ()
        document_scopes = {}
    expected_scope_ids = tuple(scope.id for scope in document_scope_list)
    plan_scope_ids = tuple(scope.id for scope in index.scopes)
    if plan_scope_ids != expected_scope_ids:
        mismatch = next(
            (
                expected
                for expected, actual in zip(expected_scope_ids, plan_scope_ids)
                if expected != actual
            ),
            None,
        )
        if mismatch is None:
            mismatch = (
                expected_scope_ids[len(plan_scope_ids)]
                if len(expected_scope_ids) > len(plan_scope_ids)
                else plan_scope_ids[len(expected_scope_ids)]
            )
        return {
            "state": "stale",
            "reason": "plan.index.scope_mismatch",
            "details": {"scope_id": mismatch},
        }
    expected_format = "ssmd" if document_format == "ssmd" else "plain"
    for scope in index.scopes:
        try:
            path = project.state_path(f"plan/{scope.path}")
            if not path.is_file():
                return {
                    "state": "stale",
                    "reason": "plan.artifact.missing",
                    "details": {"scope_id": scope.id},
                }
            if scope.sha256 is None or hash_file(path) != scope.sha256:
                return {
                    "state": "stale",
                    "reason": "plan.artifact.hash_mismatch",
                    "details": {"scope_id": scope.id},
                }
            plan = load_utterplan_v3(path)
        except PlanSchemaMismatchError as exc:
            return {
                "state": "stale",
                "reason": "plan.artifact.schema_mismatch",
                "details": {
                    "scope_id": scope.id,
                    "stored": exc.stored,
                    "required": SUPPORTED_UTTERPLAN_SCHEMA_VERSION,
                    "action": "run readio plan",
                },
            }
        except (OSError, UnicodeError, ValueError, TypeError, KeyError):
            return {
                "state": "stale",
                "reason": "plan.artifact.invalid",
                "details": {"scope_id": scope.id},
            }
        if scope.plan_id is None or plan.plan_id != scope.plan_id:
            return {
                "state": "stale",
                "reason": "plan.artifact.plan_id_mismatch",
                "details": {"scope_id": scope.id},
            }
        document_scope = document_scopes.get(scope.id)
        if document_scope is not None and scope.document_sha256 is not None:
            document_path = (
                project.workspace_path(document_scope.path)
                if project.manifest.schema_version == 4
                else project.path(document_scope.path)
            )
            if not document_path.is_file() or hash_file(document_path) != scope.document_sha256:
                return {
                    "state": "stale",
                    "reason": "plan.stale.document_changed",
                    "details": {"scope_id": scope.id},
                }
        expected_scope_format = (
            "ssmd"
            if document_scope is not None
            and document_scope.input_format.casefold() in {"ssmd", "markdown"}
            else expected_format
        )
        actual_format = plan.config.get("document_format")
        if actual_format != expected_scope_format:
            return {
                "state": "stale",
                "reason": "plan.stale.document_format_mismatch",
                "details": {
                    "scope_id": scope.id,
                    "stored": actual_format,
                    "expected": expected_scope_format,
                },
            }
    return {"state": "current", "reason": "current", "details": {"scopes": len(index.scopes)}}


def semantic_status(project: Project) -> list[dict[str, Any]]:
    paths = project.paths
    source_exists = paths["source"].is_file()
    source_sha = hash_file(paths["source"]) if source_exists else None
    if not source_exists:
        source_reason = "source.missing"
    elif source_sha != project.manifest.source_sha256:
        source_reason = "source.stale.hash_changed"
    else:
        source_reason = "current"
    source_state = "current" if source_reason == "current" else "stale"

    workspace_book: Any = None
    workspace_details: dict[str, Any] | None = None
    if project.manifest.schema_version == 4:
        refresh_command = f"ssmdconvert book refresh {project.workspace_root}"
        try:
            workspace_book = refresh_audiobook_index(project)
            workspace_details = {
                "status": "dirty" if workspace_book.workspace_dirty else "clean",
                "dirty_chapter_count": len(workspace_book.dirty_chapter_ids),
                "dirty_chapter_ids": list(workspace_book.dirty_chapter_ids),
                "refresh_command": refresh_command,
            }
        except (OSError, UnicodeError, ValueError, TypeError, KeyError) as error:
            workspace_details = {
                "status": "invalid",
                "error": str(error),
                "refresh_command": refresh_command,
            }

    if project.manifest.schema_version in {3, 4}:
        document_state = "current"
        document_reason = "current"
        document_details: dict[str, Any] = {}
        if workspace_details is not None:
            document_details["workspace"] = workspace_details
            if workspace_details["status"] == "invalid":
                document_state = "stale"
                document_reason = "document.workspace.invalid"
        try:
            document_index = project.load_document_index()
            if project.manifest.kind == "audiobook":
                source_numbers = tuple(scope.source_number for scope in document_index.scopes)
                valid_source_numbers = tuple(
                    number
                    for number in source_numbers
                    if isinstance(number, int) and not isinstance(number, bool) and number >= 1
                )
                if (
                    len(valid_source_numbers) != len(source_numbers)
                    or tuple(sorted(set(valid_source_numbers))) != valid_source_numbers
                    or tuple(document_index.selection) != valid_source_numbers
                ):
                    raise ValueError("document index selection/order is invalid")
            for scope in document_index.scopes:
                scope_path = (
                    project.workspace_path(scope.path)
                    if project.manifest.schema_version == 4
                    else project.state_path(scope.path)
                )
                if not scope_path.is_file():
                    document_state = "stale"
                    document_reason = (
                        "document.chapter.missing"
                        if scope.kind == "chapter"
                        else "document.scope.missing"
                    )
                    document_details["scope_id"] = scope.id
                    break
                document = project.load_document_scope(scope)
                if scope.input_format.casefold() == "ssmd":
                    try:
                        parse_ssmd_09(document.text, source_path=scope_path)
                    except SSMDInputError:
                        document_state = "stale"
                        document_reason = "document.scope.invalid"
                        document_details["scope_id"] = scope.id
                        break
                elif scope.input_format.casefold() not in {"text", "markdown"}:
                    raise ValueError(f"unsupported semantic input format: {scope.input_format}")
        except (OSError, UnicodeError, ValueError, TypeError, KeyError):
            document_state = "stale"
            document_reason = "document.index.invalid"

        if not paths["plan_index"].is_file():
            plan_state, plan_reason, plan_details = "stale", "plan.index.missing", {}
        elif document_state != "current":
            plan_state = "stale"
            plan_reason = "plan.stale.document_changed"
            plan_details = dict(document_details)
        else:
            validation = _plan_artifact_status(project, "text")
            plan_state = validation["state"]
            plan_reason = validation["reason"]
            plan_details = validation["details"]
        return [
            {
                "stage": "source",
                "state": source_state,
                "reason": source_reason,
                "sha256": source_sha,
            },
            {
                "stage": "document",
                "state": document_state,
                "reason": document_reason,
                **document_details,
            },
            {
                "stage": "plan",
                "state": plan_state,
                "reason": plan_reason,
                **plan_details,
            },
        ]

    document_format = project.manifest.source_format
    document_state = "stale"
    if (
        paths["document_metadata"].is_file()
        and paths["document_text"].is_file()
        and source_sha is not None
    ):
        try:
            metadata = json.loads(paths["document_metadata"].read_text(encoding="utf-8"))
            input_format = metadata.get("input_format", project.manifest.source_format)
            document_format = metadata.get("document_format") or (
                "ssmd" if input_format == "ssmd" else "text"
            )
            document_state = "current" if metadata.get("source_sha256") == source_sha else "stale"
        except (OSError, UnicodeError, ValueError):
            document_state = "stale"
    if not paths["plan_index"].is_file():
        plan_state, plan_reason, plan_details = "stale", "plan.index.missing", {}
    elif document_state != "current":
        plan_state, plan_reason, plan_details = "stale", "plan.stale.source_changed", {}
    else:
        validation = _plan_artifact_status(project, document_format)
        plan_state = validation["state"]
        plan_reason = validation["reason"]
        plan_details = validation["details"]
    return [
        {
            "stage": "source",
            "state": source_state,
            "reason": source_reason,
            "sha256": source_sha,
        },
        {
            "stage": "document",
            "state": document_state,
            "reason": "current" if document_state == "current" else "document.stale.source_changed",
        },
        {
            "stage": "plan",
            "state": plan_state,
            "reason": plan_reason,
            **plan_details,
        },
    ]


__all__ = [
    "PlanSchemaMismatchError",
    "PlannedScope",
    "ProjectPlanningResult",
    "ResolvedSemanticPlanning",
    "compile_project_scope",
    "load_primary_scope_plan",
    "load_scope_plan",
    "load_utterplan_v3",
    "plan_document",
    "plan_project",
    "plan_project_scope",
    "prepare_project_document",
    "resolve_semantic_planning",
    "semantic_status",
]
