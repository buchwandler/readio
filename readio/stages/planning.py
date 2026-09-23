"""Engine-free semantic planning stage."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utterplan import CURRENT_SCHEMA_VERSION, UtterancePlan

from ..document import InputDocument
from ..markdown import markdown_to_speech
from ..planning.compiler import CompiledSemanticPlan, compile_semantic_plan
from ..planning.policy import PlanningPolicy
from ..project import (
    Project,
    atomic_write_bytes,
    atomic_write_json,
    hash_file,
    project_lock,
    sha256_bytes,
)
from ..project_model import DocumentScope, PlanIndex, PlanScope
from ..reader import prepare_input_document


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


class PlanSchemaMismatchError(ValueError):
    """A persisted Readio semantic plan is not the supported Utterplan schema."""

    def __init__(self, stored: object) -> None:
        self.stored = stored
        super().__init__(
            f"Utterplan schema {stored!r} is not supported; expected {CURRENT_SCHEMA_VERSION}"
        )


def resolve_semantic_planning(cfg: Any, document: InputDocument) -> ResolvedSemanticPlanning:
    prepared = prepare_input_document(document)
    planner_document_format = "ssmd" if prepared.format == "ssmd" else "plain"
    policy = PlanningPolicy.from_semantic_config(cfg, document_format=planner_document_format)
    compiled = compile_semantic_plan(prepared, planning=policy)
    return ResolvedSemanticPlanning(prepared, policy, compiled)


def _write_plan_artifact(path: Path, compiled: CompiledSemanticPlan) -> str:
    serialized = compiled.serialized or compiled.plan.to_json().encode("utf-8")
    atomic_write_bytes(path, serialized)
    return sha256_bytes(serialized)


def prepare_project_document(project: Project) -> InputDocument:
    """Refresh the normalized document snapshot from the editable project source."""
    paths = project.paths
    source = paths["source"]
    raw = source.read_text(encoding="utf-8")
    metadata = __import__("json").loads(paths["document_metadata"].read_text(encoding="utf-8"))
    input_format = metadata.get("input_format", project.manifest.source_format)
    if input_format == "markdown":
        normalized = markdown_to_speech(raw)
        document_format = "text"
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
) -> PlannedScope:
    """Compile one document scope without writing artifacts or mutating indexes."""
    if not scope.id or "/" in scope.id or "\\" in scope.id or scope.id in {".", ".."}:
        raise ValueError("scope_id must be a simple identifier")
    resolved = resolve_semantic_planning(cfg, document)
    relative = (
        Path("document.utterplan.json")
        if scope.id == "document" and scope.kind == "document"
        else Path("chapters") / f"{scope.id}.utterplan.json"
    )
    serialized = resolved.compiled.serialized or resolved.compiled.plan.to_json().encode("utf-8")
    input_path = project.path(scope.path)
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
) -> CompiledSemanticPlan:
    """Compile and replace one independent plan scope while preserving others."""
    scope = DocumentScope(
        id=scope_id,
        kind=kind,
        path="",
        input_format=document.format,
        title=title,
    )
    with project_lock(project, operation="plan-scope"):
        planned = compile_project_scope(
            project, cfg, scope, document, document_sha256=document_sha256
        )
        plan_path = project.root / "plan" / planned.scope.path
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
        old_scopes = ()
        if project.paths["plan_index"].is_file():
            old_scopes = project.load_plan_index().scopes
        scopes = tuple(replacement if item.id == scope_id else item for item in old_scopes)
        if not any(item.id == scope_id for item in old_scopes):
            scopes = (*scopes, replacement)
        atomic_write_json(project.paths["plan_index"], PlanIndex(scopes=tuple(scopes)).to_dict())
        return planned.compiled


def plan_project(project: Project, cfg: Any) -> ProjectPlanningResult:
    """Plan every persisted document scope, then atomically replace the plan index."""
    with project_lock(project, operation="plan"):
        document_scopes = project.document_scopes()
        planned_scopes = []
        for scope in document_scopes:
            if project.manifest.schema_version == 1 or (
                project.manifest.kind == "document"
                and len(document_scopes) == 1
                and scope.id == "document"
            ):
                document = prepare_project_document(project)
            else:
                document = project.load_document_scope(scope)
            planned_scopes.append(compile_project_scope(project, cfg, scope, document))

        for planned in planned_scopes:
            plan_path = project.root / "plan" / planned.scope.path
            _write_plan_artifact(plan_path, planned.compiled)
        index = PlanIndex(scopes=tuple(item.scope for item in planned_scopes))
        atomic_write_json(project.paths["plan_index"], index.to_dict())
        return ProjectPlanningResult(scopes=tuple(planned_scopes))


def plan_document(document: InputDocument, cfg: Any, output: Path) -> CompiledSemanticPlan:
    resolved = resolve_semantic_planning(cfg, document)
    _write_plan_artifact(output, resolved.compiled)
    return resolved.compiled


def load_utterplan_v2(path: Path) -> UtterancePlan:
    """Load a Readio semantic plan without invoking Utterplan migration."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("format") != "utterplan":
        raise ValueError("semantic plan artifact is not an Utterplan document")
    stored = data.get("schema_version")
    if stored != CURRENT_SCHEMA_VERSION:
        raise PlanSchemaMismatchError(stored)
    return UtterancePlan.from_dict(data)


def load_scope_plan(project: Project, scope: PlanScope) -> UtterancePlan:
    return load_utterplan_v2(project.root / "plan" / scope.path)


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
            path = project.path(str(Path("plan") / scope.path))
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
            plan = load_utterplan_v2(path)
        except PlanSchemaMismatchError as exc:
            return {
                "state": "stale",
                "reason": "plan.artifact.schema_mismatch",
                "details": {
                    "scope_id": scope.id,
                    "stored": exc.stored,
                    "required": CURRENT_SCHEMA_VERSION,
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
            document_path = project.path(document_scope.path)
            if not document_path.is_file() or hash_file(document_path) != scope.document_sha256:
                return {
                    "state": "stale",
                    "reason": "plan.stale.document_changed",
                    "details": {"scope_id": scope.id},
                }
        expected_scope_format = (
            "ssmd"
            if document_scope is not None and document_scope.input_format == "ssmd"
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
                    "expected": expected_format,
                },
            }
    return {"state": "current", "reason": "current", "details": {"scopes": len(index.scopes)}}


def semantic_status(project: Project) -> list[dict[str, Any]]:
    paths = project.paths
    source_exists = paths["source"].is_file()
    source_sha = hash_file(paths["source"]) if source_exists else None
    source_state = "current" if source_exists else "stale"
    document_state = "stale"
    if project.manifest.kind == "audiobook":
        if source_sha is None:
            source_reason = "source.missing"
        elif source_sha != project.manifest.source_sha256:
            source_reason = "source.stale.hash_changed"
        else:
            source_reason = "current"
        source_state = "current" if source_reason == "current" else "stale"
        document_state = "current"
        document_reason = "current"
        document_details: dict[str, Any] = {}
        try:
            document_index = project.load_document_index()
            source_numbers = tuple(scope.source_number for scope in document_index.scopes)
            if (
                any(number is None or number < 1 for number in source_numbers)
                or tuple(sorted(set(source_numbers))) != source_numbers
                or tuple(document_index.selection) != source_numbers
            ):
                raise ValueError("document index selection/order is invalid")
            for scope in document_index.scopes:
                if not project.path(scope.path).is_file():
                    document_state = "stale"
                    document_reason = "document.chapter.missing"
                    document_details = {"scope_id": scope.id}
                    break
        except (OSError, UnicodeError, ValueError, TypeError, KeyError):
            document_state = "stale"
            document_reason = "document.index.invalid"
        if document_state == "current":
            if not paths["plan_index"].is_file():
                plan_state, plan_reason, plan_details = "stale", "plan.index.missing", {}
            else:
                validation = _plan_artifact_status(project, "text")
                plan_state = validation["state"]
                plan_reason = validation["reason"]
                plan_details = validation["details"]
        else:
            plan_state = "stale"
            plan_reason = "plan.stale.document_changed"
            plan_details = dict(document_details)
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
    if (
        paths["document_metadata"].is_file()
        and paths["document_text"].is_file()
        and source_sha is not None
    ):
        try:
            metadata = __import__("json").loads(
                paths["document_metadata"].read_text(encoding="utf-8")
            )
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
            "reason": "current" if source_state == "current" else "source.missing",
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
    "load_utterplan_v2",
    "plan_document",
    "plan_project",
    "plan_project_scope",
    "prepare_project_document",
    "resolve_semantic_planning",
    "semantic_status",
]
