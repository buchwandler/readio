"""Engine-free semantic planning stage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from ..project_model import PlanIndex, PlanScope
from ..reader import prepare_input_document


@dataclass(frozen=True, slots=True)
class ResolvedSemanticPlanning:
    document: InputDocument
    policy: PlanningPolicy
    compiled: CompiledSemanticPlan


def resolve_semantic_planning(cfg: Any, document: InputDocument) -> ResolvedSemanticPlanning:
    prepared = prepare_input_document(document)
    planner_document_format = "ssmd" if prepared.format == "ssmd" else "plain"
    policy = PlanningPolicy.from_semantic_config(
        cfg, document_format=planner_document_format
    )
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


def plan_project_scope(
    project: Project,
    cfg: Any,
    scope_id: str,
    document: InputDocument,
    *,
    kind: str = "chapter",
    title: str | None = None,
) -> CompiledSemanticPlan:
    """Compile/replace one independent scope while preserving other scopes."""
    if not scope_id or "/" in scope_id or "\\" in scope_id or scope_id in {".", ".."}:
        raise ValueError("scope_id must be a simple identifier")
    with project_lock(project, operation="plan-scope"):
        resolved = resolve_semantic_planning(cfg, document)
        relative = Path("chapters") / f"{scope_id}.utterplan.json"
        path = project.root / "plan" / relative
        plan_sha = _write_plan_artifact(path, resolved.compiled)
        old_scopes = ()
        if project.paths["plan_index"].is_file():
            old_scopes = project.load_plan_index().scopes
        replacement = PlanScope(
            id=scope_id,
            kind=kind,
            path=relative.as_posix(),
            title=title,
            plan_id=resolved.compiled.plan_id,
            sha256=plan_sha,
        )
        scopes = tuple(replacement if item.id == scope_id else item for item in old_scopes)
        if not any(item.id == scope_id for item in old_scopes):
            scopes = (*scopes, replacement)
        atomic_write_json(project.paths["plan_index"], PlanIndex(scopes=tuple(scopes)).to_dict())
        return resolved.compiled


def plan_project(project: Project, cfg: Any) -> CompiledSemanticPlan:
    with project_lock(project, operation="plan"):
        document = prepare_project_document(project)
        resolved = resolve_semantic_planning(cfg, document)
        paths = project.paths
        plan_path = project.root / "plan" / "document.utterplan.json"
        plan_sha = _write_plan_artifact(plan_path, resolved.compiled)
        index = PlanIndex(
            scopes=(
                PlanScope(
                    id="document",
                    kind="document",
                    path="document.utterplan.json",
                    title=project.manifest.name,
                    plan_id=resolved.compiled.plan_id,
                    sha256=plan_sha,
                ),
            )
        )
        atomic_write_json(paths["plan_index"], index.to_dict())
        return resolved.compiled


def plan_document(document: InputDocument, cfg: Any, output: Path) -> CompiledSemanticPlan:
    resolved = resolve_semantic_planning(cfg, document)
    _write_plan_artifact(output, resolved.compiled)
    return resolved.compiled


def load_scope_plan(project: Project, scope: PlanScope | None = None) -> Any:
    from utterplan import UtterancePlan

    scope = scope or project.load_plan_index().scopes[0]
    return UtterancePlan.load(project.root / "plan" / scope.path)


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
            from utterplan import UtterancePlan
            plan = UtterancePlan.load(path)
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
        actual_format = plan.config.get("document_format")
        if actual_format != expected_format:
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
    document_format = project.manifest.source_format
    if paths["document_metadata"].is_file() and paths["document_text"].is_file() and source_sha is not None:
        try:
            metadata = __import__("json").loads(
                paths["document_metadata"].read_text(encoding="utf-8")
            )
            input_format = metadata.get("input_format", project.manifest.source_format)
            document_format = metadata.get("document_format") or (
                "ssmd" if input_format == "ssmd" else "text"
            )
            document_state = (
                "current"
                if metadata.get("source_sha256") == source_sha
                else "stale"
            )
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
    "ResolvedSemanticPlanning",
    "load_scope_plan",
    "plan_document",
    "plan_project",
    "plan_project_scope",
    "prepare_project_document",
    "resolve_semantic_planning",
    "semantic_status",
]
