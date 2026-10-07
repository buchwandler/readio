"""Engine-free semantic planning stage."""

from __future__ import annotations

import json
import os
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast

from utterplan import (
    PlanFormatError,
    PlannerProgressEvent,
    PlanningAttempt,
    PlanRenderabilityError,
    preflight_renderability,
)
from utterplan import __version__ as utterplan_version

from ..audiobook import refresh_audiobook_index
from ..document import InputDocument, document_from_text
from ..errors import (
    InvalidStoredPlanError,
    ProjectPlanAttemptError,
    ProjectPlanRenderabilityError,
    SSMDInputError,
)
from ..integrations.ssmdconvert import ssmdconvert_version
from ..jsonutil import JsonValue, json_value
from ..planning import (
    SUPPORTED_UTTERPLAN_SCHEMA_VERSION,
    CompiledSemanticPlan,
    LegacyPlanArtifactError,
    PlanningPolicy,
    PlanSchemaMismatchError,
    UtterancePlan,
    compile_semantic_plan,
    load_current_utterplan,
    serialize_utterplan,
)
from ..project import (
    Project,
    atomic_write_bytes,
    atomic_write_json,
    hash_file,
    project_lock,
    read_json,
    sha256_bytes,
)
from ..project_model import DocumentScope, PlanIndex, PlanScope
from ..project_settings import (
    project_planning_config,
    project_planning_settings_fingerprint,
    project_settings_from_manifest,
)
from ..project_settings import semantic_planner_fingerprint as _semantic_planner_fingerprint
from ..reader import prepare_input_document
from ..ssmd import (
    SSMD_SEMANTICS_VERSION,
    language_detection_hint_from_header,
    parse_ssmd_09,
    parse_ssmd_structure_09,
)


def semantic_planner_fingerprint(planning_config: Mapping[str, Any]) -> str:
    semantic_config = {
        key: value for key, value in planning_config.items() if key != "renderability_mode"
    }
    return _semantic_planner_fingerprint(
        semantic_config,
        utterplan_schema_version=SUPPORTED_UTTERPLAN_SCHEMA_VERSION,
        utterplan_version=utterplan_version,
        ssmd_version=SSMD_SEMANTICS_VERSION,
        ssmdconvert_version=ssmdconvert_version,
    )


def _legacy_semantic_planner_fingerprint(planning_config: Mapping[str, Any]) -> str:
    """Return the pre-v2 fingerprint, which included renderability mode."""
    return _semantic_planner_fingerprint(
        planning_config,
        utterplan_schema_version=SUPPORTED_UTTERPLAN_SCHEMA_VERSION,
        utterplan_version=utterplan_version,
        ssmd_version=SSMD_SEMANTICS_VERSION,
        ssmdconvert_version=ssmdconvert_version,
    )


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
    renderability_mode: Literal["strict", "repair"] = "repair"
    renderability_guaranteed: bool = True
    repairs: int = 0
    diagnostics: tuple[dict[str, JsonValue], ...] = ()
    attempt_id: str | None = None
    activated: bool = False
    reused_scopes: tuple[str, ...] = ()
    rebuilt_scopes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectPlanningProgress:
    """Internal planning progress enriched with project-scope context."""

    kind: Literal[
        "attempt.started",
        "attempt.persisted",
        "attempt.promoting",
        "attempt.activated",
        "attempt.blocked",
        "scope.started",
        "scope.reused",
        "scope.persisted",
        "scope.completed",
        "scope.failed",
        "renderability.started",
        "renderability.completed",
        "renderability.failed",
        "planner",
    ]
    attempt_id: str | None = None
    scope_id: str | None = None
    scope_index: int | None = None
    scope_total: int | None = None
    planner_event: PlannerProgressEvent | None = None
    details: Mapping[str, JsonValue] | None = None


PlannerProgressCallback = Callable[[PlannerProgressEvent], None]
PlanningProgressCallback = Callable[[ProjectPlanningProgress], None]


def _prepare_planning_policy(
    cfg: Any,
    document: InputDocument,
    renderability_mode: Literal["strict", "repair"] | None,
) -> tuple[InputDocument, PlanningPolicy]:
    prepared = prepare_input_document(document)
    planner_document_format = "ssmd" if prepared.format == "ssmd" else "plain"
    policy = PlanningPolicy.from_semantic_config(cfg, document_format=planner_document_format)
    if renderability_mode is not None:
        policy = replace(policy, renderability_mode=renderability_mode)
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
    return prepared, policy


def _planning_fingerprint(
    cfg: Any,
    document: InputDocument,
    renderability_mode: Literal["strict", "repair"],
) -> str:
    _, policy = _prepare_planning_policy(cfg, document, renderability_mode)
    return semantic_planner_fingerprint(asdict(policy.to_planner_config()))


def resolve_semantic_planning(
    cfg: Any,
    document: InputDocument,
    *,
    on_progress: PlannerProgressCallback | None = None,
    renderability_mode: Literal["strict", "repair"] | None = None,
) -> ResolvedSemanticPlanning:
    prepared, policy = _prepare_planning_policy(cfg, document, renderability_mode)
    compiled = compile_semantic_plan(prepared, planning=policy, on_progress=on_progress)
    return ResolvedSemanticPlanning(prepared, policy, compiled)


def _renderability_summary(plan: UtterancePlan) -> dict[str, JsonValue]:
    planning = plan.document_metadata.get("planning")
    renderability = planning.get("renderability") if isinstance(planning, Mapping) else None
    if not isinstance(renderability, Mapping):
        return {
            "checked_segments": len(plan.segments),
            "repair_count": 0,
            "guaranteed": False,
        }
    checked_segments = renderability.get("checked_segments")
    repair_count = renderability.get("repair_count")
    return {
        "checked_segments": checked_segments
        if isinstance(checked_segments, int)
        else len(plan.segments),
        "repair_count": repair_count if isinstance(repair_count, int) else 0,
        "guaranteed": renderability.get("guaranteed") is True,
    }


def _enrich_renderability_issues(
    scope: DocumentScope,
    issues: tuple[Any, ...],
) -> tuple[dict[str, JsonValue], ...]:
    enriched: list[dict[str, JsonValue]] = []
    for issue in issues:
        enriched.append(
            {
                "scope_id": scope.id,
                "scope_kind": scope.kind,
                "scope_title": scope.title,
                "scope_number": scope.source_number,
                "source_path": Path(scope.path).as_posix(),
                "code": issue.code,
                "reason": issue.reason,
                "segment_id": issue.segment_id,
                "segment_index": issue.segment_index,
                "text": issue.text,
                "spoken_start": issue.spoken_start,
                "spoken_end": issue.spoken_end,
                "structural_start": issue.structural_start,
                "structural_end": issue.structural_end,
                "source_start": issue.source_start,
                "source_end": issue.source_end,
                "line": issue.line,
                "column": issue.column,
                "end_line": issue.end_line,
                "end_column": issue.end_column,
                "source_excerpt": issue.source_excerpt,
                "token_pos": list(issue.token_pos),
                "token_ids": list(issue.token_ids),
                "repair": issue.repair,
                "repair_safe": (
                    issue.repair_assessment.safe if issue.repair_assessment is not None else None
                ),
                "repair_action": (
                    issue.repair_assessment.action if issue.repair_assessment is not None else None
                ),
                "repair_blockers": (
                    list(issue.repair_assessment.blockers)
                    if issue.repair_assessment is not None
                    else []
                ),
                "source_context": [list(item) for item in issue.source_context],
                "source_caret": issue.source_caret,
                "spoken_context": issue.spoken_context,
                "hint": issue.hint,
                "repair_command": ProjectPlanRenderabilityError.repair_command,
            }
        )
    return tuple(enriched)


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
    renderability_mode: Literal["strict", "repair"] | None = None,
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
            if event.kind == "phase.started" and event.phase == "segmentation":
                on_progress(
                    ProjectPlanningProgress(
                        kind="renderability.started",
                        scope_id=scope.id,
                        scope_index=scope_index,
                        scope_total=scope_total,
                        details={"mode": renderability_mode or "strict"},
                    )
                )
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
    resolved = resolve_semantic_planning(
        planning_config,
        document,
        on_progress=planner_callback,
        renderability_mode=renderability_mode,
    )

    if on_progress is not None:
        on_progress(
            ProjectPlanningProgress(
                kind="renderability.completed",
                scope_id=scope.id,
                scope_index=scope_index,
                scope_total=scope_total,
                details={
                    **_renderability_summary(resolved.compiled.plan),
                    "mode": resolved.policy.renderability_mode,
                },
            )
        )
    relative = (
        Path("document.utterplan.toml")
        if scope.id == "document" and scope.kind == "document"
        else Path("chapters") / f"{scope.id}.utterplan.toml"
    )
    serialized = resolved.compiled.serialized or serialize_utterplan(resolved.compiled.plan)
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
        semantic_planner_fingerprint=semantic_planner_fingerprint(resolved.compiled.plan.config),
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
            semantic_planner_fingerprint=planned.scope.semantic_planner_fingerprint,
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


_PLAN_ATTEMPT_SCHEMA_VERSION = 1
_READIO_SEMANTIC_PLANNING_VERSION = 2


def _attempt_manifest_path(project: Project, attempt_id: str) -> Path:
    if not re.fullmatch(r"plan-attempt-[0-9a-f]{16}", attempt_id):
        raise ValueError(f"invalid planning attempt ID: {attempt_id!r}")
    return project.state_path(f"plan/attempts/{attempt_id}/attempt.json")


def _load_latest_attempt(project: Project) -> dict[str, Any] | None:
    latest_path = project.state_path("plan/attempts/latest.json")
    if not latest_path.is_file():
        return None
    try:
        pointer = read_json(latest_path)
        attempt_id = pointer.get("attempt_id")
        if not isinstance(attempt_id, str):
            return None
        manifest = read_json(_attempt_manifest_path(project, attempt_id))
        if (
            manifest.get("schema_version") != _PLAN_ATTEMPT_SCHEMA_VERSION
            or manifest.get("attempt_id") != attempt_id
            or not isinstance(manifest.get("scopes"), list)
        ):
            return None
        return manifest
    except (OSError, UnicodeError, ValueError):
        return None


_ATTEMPT_STATUSES = {"renderable", "repaired", "blocked", "incomplete"}
_SCOPE_ATTEMPT_STATUSES = {"processing", "renderable", "repaired", "blocked", "failed"}


def _attempt_artifact_path(project: Project, attempt_id: str, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts or "\\" in relative_path:
        raise ValueError("attempt artifact path is not a contained relative path")
    attempt_dir = _attempt_manifest_path(project, attempt_id).parent
    artifact = project.state_path((Path("plan/attempts") / attempt_id / relative).as_posix())
    if attempt_dir not in artifact.parents:
        raise ValueError("attempt artifact path escapes its attempt directory")
    return artifact


def _read_plan_attempt(
    project: Project, attempt_id: str
) -> tuple[dict[str, Any], list[dict[str, JsonValue]]]:
    try:
        manifest_path = _attempt_manifest_path(project, attempt_id)
        manifest = read_json(manifest_path)
        if manifest.get("schema_version") != _PLAN_ATTEMPT_SCHEMA_VERSION:
            raise ValueError("unsupported attempt schema version")
        if manifest.get("attempt_id") != attempt_id:
            raise ValueError("attempt ID does not match its manifest")
        if manifest.get("status") not in _ATTEMPT_STATUSES:
            raise ValueError("attempt has an unsupported status")
        if manifest.get("renderability_mode") not in {"strict", "repair"}:
            raise ValueError("attempt has an unsupported renderability mode")
        if not isinstance(manifest.get("created_at"), str):
            raise TypeError("attempt is missing its creation timestamp")
        if not isinstance(manifest.get("activated"), bool):
            raise TypeError("attempt activation state must be a boolean")
        if manifest["activated"] and manifest["status"] not in {"renderable", "repaired"}:
            raise ValueError("a blocked or incomplete attempt cannot be activated")
        scope_rows = manifest.get("scopes")
        if not isinstance(scope_rows, list):
            raise TypeError("attempt scopes must be a list")
        seen_scope_ids: set[str] = set()
        for row in scope_rows:
            if not isinstance(row, dict):
                raise TypeError("attempt scope entries must be objects")
            scope_id = row.get("scope_id")
            if not isinstance(scope_id, str) or not scope_id or scope_id in seen_scope_ids:
                raise ValueError("attempt scope IDs must be unique non-empty strings")
            seen_scope_ids.add(scope_id)
            if row.get("status") not in _SCOPE_ATTEMPT_STATUSES:
                raise ValueError(f"attempt scope {scope_id!r} has an unsupported status")
            candidate_relpath = row.get("candidate_path")
            if candidate_relpath is not None:
                if not isinstance(candidate_relpath, str):
                    raise ValueError(f"attempt scope {scope_id!r} has an invalid candidate path")
                candidate_path = _attempt_artifact_path(project, attempt_id, candidate_relpath)
                if not candidate_path.is_file():
                    raise ValueError(f"attempt candidate for scope {scope_id!r} is missing")
                expected_sha = row.get("candidate_sha256")
                if not isinstance(expected_sha, str) or hash_file(candidate_path) != expected_sha:
                    raise ValueError(
                        f"attempt candidate for scope {scope_id!r} failed its digest check"
                    )
                candidate_kind = row.get("candidate_kind")
                if candidate_kind in {None, "canonical_plan"}:
                    plan = load_current_utterplan(candidate_path)
                    if row.get("plan_id") and plan.plan_id != row["plan_id"]:
                        raise ValueError(f"attempt plan ID mismatch for scope {scope_id!r}")
                elif candidate_kind == "utterplan_attempt":
                    PlanningAttempt.load(candidate_path)
                elif candidate_kind == "diagnostic_json":
                    read_json(candidate_path)
                else:
                    raise ValueError(f"attempt candidate kind is invalid for scope {scope_id!r}")
            elif row.get("status") in {"renderable", "repaired", "blocked"}:
                raise ValueError(f"completed attempt scope {scope_id!r} has no artifact")
        issue_doc = read_json(manifest_path.parent / "issues.json")
        if (
            issue_doc.get("schema_version") != _PLAN_ATTEMPT_SCHEMA_VERSION
            or issue_doc.get("attempt_id") != attempt_id
            or not isinstance(issue_doc.get("issues"), list)
            or any(not isinstance(issue, dict) for issue in issue_doc["issues"])
        ):
            raise ValueError("attempt issue artifact is malformed")
        issues = cast(list[dict[str, JsonValue]], issue_doc["issues"])
        if manifest.get("issue_count", 0) != len(issues):
            raise ValueError("attempt issue count does not match its issue artifact")
        return manifest, issues
    except ProjectPlanAttemptError:
        raise
    except Exception as exc:
        raise ProjectPlanAttemptError(attempt_id, str(exc)) from exc


def _read_latest_plan_attempt(
    project: Project,
) -> tuple[dict[str, Any], list[dict[str, JsonValue]]] | None:
    latest_path = project.state_path("plan/attempts/latest.json")
    if not latest_path.is_file():
        return None
    try:
        pointer = read_json(latest_path)
        if pointer.get("schema_version") != _PLAN_ATTEMPT_SCHEMA_VERSION:
            raise ValueError("latest attempt pointer has an unsupported schema version")
        attempt_id = pointer.get("attempt_id")
        if not isinstance(attempt_id, str):
            raise TypeError("latest attempt pointer is missing an attempt ID")
        return _read_plan_attempt(project, attempt_id)
    except ProjectPlanAttemptError:
        raise
    except Exception as exc:
        raise ProjectPlanAttemptError(None, str(exc)) from exc


def _load_attempt_scope_plan(
    project: Project, attempt_id: str, scope_record: Mapping[str, Any]
) -> UtterancePlan | None:
    relative_path = scope_record.get("candidate_path")
    if not isinstance(relative_path, str):
        return None
    candidate_path = _attempt_artifact_path(project, attempt_id, relative_path)
    candidate_kind = scope_record.get("candidate_kind")
    if candidate_kind == "utterplan_attempt":
        attempt = PlanningAttempt.load(candidate_path)
        if attempt.ok:
            return attempt.candidate.to_plan().with_identity()
        # Blocked drafts are inspection-only and must never enter planning or synthesis.
        draft_plan = getattr(attempt.candidate, "_plan", None)
        if not isinstance(draft_plan, UtterancePlan):
            raise ValueError("blocked attempt does not contain an inspectable draft plan")
        return draft_plan
    if candidate_kind == "diagnostic_json":
        return None
    return load_current_utterplan(candidate_path)


def _source_position(text: str, offset: int) -> tuple[int, int]:
    bounded = max(0, min(offset, len(text)))
    line = text.count("\n", 0, bounded) + 1
    previous_newline = text.rfind("\n", 0, bounded)
    return line, bounded - previous_newline


def _segment_source_span(plan: UtterancePlan, segment: Any) -> tuple[int, int] | None:
    start = segment.structural_start
    end = segment.structural_end
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    source_text = plan.source.text
    if plan.source.format not in {"ssmd", "markdown"}:
        if 0 <= start <= end <= len(source_text):
            return start, end
        return None
    try:
        structure = parse_ssmd_structure_09(source_text)
        overlapping = [
            span for span in structure.text_spans if span.char_end > start and span.char_start < end
        ]
        if not overlapping:
            return None
        return overlapping[0].source_start, overlapping[-1].source_end
    except (ValueError, TypeError, AttributeError):
        return None


def _segment_detail(
    plan: UtterancePlan,
    segment: Any,
    *,
    scope_id: str,
    source_path: str | None,
    issue_records: Sequence[Mapping[str, Any]],
    source_context: int,
) -> dict[str, JsonValue]:
    structural_start = segment.structural_start
    structural_end = segment.structural_end
    spoken_start = segment.spoken_start
    spoken_end = segment.spoken_end
    structural_text = plan.texts.structural
    spoken_text = plan.texts.spoken
    source_text = plan.source.text
    source_span = _segment_source_span(plan, segment)
    source_start, source_end = source_span if source_span is not None else (None, None)
    source_excerpt = (
        source_text[source_start:source_end]
        if source_start is not None and source_end is not None
        else None
    )
    start_line = (
        _source_position(source_text, source_start)[0] if source_start is not None else None
    )
    context_lines: list[JsonValue] = []
    if start_line is not None and source_context >= 0:
        lines = source_text.splitlines()
        first = max(1, start_line - source_context)
        last = min(len(lines), start_line + source_context)
        context_lines = [
            {"line": number, "text": lines[number - 1]} for number in range(first, last + 1)
        ]
    unit_ids = [unit.id for unit in plan.units if segment.id in unit.segment_ids]
    segment_issue = next(
        (
            dict(issue)
            for issue in issue_records
            if issue.get("scope_id") == scope_id and issue.get("segment_id") == segment.id
        ),
        None,
    )
    if segment_issue is None:
        report = preflight_renderability(plan)
        current_issue = next(
            (issue for issue in report.issues if issue.segment_id == segment.id), None
        )
        renderability: dict[str, JsonValue] = {
            "status": "blocked" if current_issue is not None else "renderable"
        }
        if current_issue is not None:
            renderability.update(cast(dict[str, JsonValue], json_value(current_issue)))
            assessment = current_issue.repair_assessment
            if assessment is not None:
                renderability["repair_assessment"] = cast(JsonValue, json_value(assessment))
    else:
        renderability = {
            "status": "blocked",
            "issue": cast(dict[str, JsonValue], json_value(segment_issue)),
        }
    tokens = plan.tokens_for_segment(segment.id)
    annotations = [
        annotation
        for annotation in plan.annotations
        if annotation.id in set(segment.annotation_ids)
    ]
    boundaries_before = [event for event in plan.boundaries if event.position == spoken_start]
    boundaries_after = [event for event in plan.boundaries if event.position == spoken_end]
    semantic_boundaries = [
        event for event in plan.semantic_boundaries if event.position in {spoken_start, spoken_end}
    ]
    directives = segment.directives
    return cast(
        dict[str, JsonValue],
        json_value(
            {
                "scope_id": scope_id,
                "scope_path": source_path,
                "segment_id": segment.id,
                "unit_ids": unit_ids,
                "language": segment.language,
                "effective_voice": directives.voice,
                "effective_prosody": directives.prosody,
                "directives": directives,
                "source": {
                    "format": plan.source.format,
                    "span": {"start": source_start, "end": source_end},
                    "text": source_excerpt,
                    "context": context_lines,
                    "line": start_line,
                },
                "structural": {
                    "span": {"start": structural_start, "end": structural_end},
                    "text": (
                        structural_text[structural_start:structural_end]
                        if isinstance(structural_start, int) and isinstance(structural_end, int)
                        else None
                    ),
                },
                "spoken": {
                    "span": {"start": spoken_start, "end": spoken_end},
                    "text": spoken_text[spoken_start:spoken_end],
                },
                "tokens": tokens,
                "annotations": annotations,
                "annotation_ids": segment.annotation_ids,
                "boundaries_before": boundaries_before,
                "boundaries_after": boundaries_after,
                "semantic_boundaries": semantic_boundaries,
                "renderability": renderability,
            }
        ),
    )


def _issue_for_inspection(
    issue: Mapping[str, Any],
    *,
    scope_id: str,
    plan: UtterancePlan | None,
) -> dict[str, JsonValue]:
    result = dict(issue)
    result["scope_id"] = scope_id
    segment_id = result.get("segment_id")
    if plan is not None and isinstance(segment_id, str):
        result["unit_id"] = next(
            (unit.id for unit in plan.units if segment_id in unit.segment_ids),
            None,
        )
    return cast(dict[str, JsonValue], json_value(result))


def _attempt_ref(manifest: Mapping[str, Any] | None) -> dict[str, JsonValue] | None:
    if manifest is None:
        return None
    return cast(
        dict[str, JsonValue],
        json_value(
            {
                "attempt_id": manifest["attempt_id"],
                "status": manifest["status"],
                "activated": manifest["activated"],
                "renderability_mode": manifest["renderability_mode"],
                "created_at": manifest["created_at"],
                "parent_attempt_id": manifest.get("parent_attempt_id"),
                "scope_count": len(manifest.get("scopes", [])),
                "issue_count": manifest.get("issue_count", 0),
                "repair_count": manifest.get("repair_count", 0),
            }
        ),
    )


def _active_attempt_ref(project: Project) -> dict[str, Any] | None:
    attempts_root = project.state_path("plan/attempts")
    if not attempts_root.is_dir():
        return None
    candidates: list[dict[str, Any]] = []
    for child in attempts_root.iterdir():
        if not child.is_dir() or not child.name.startswith("plan-attempt-"):
            continue
        try:
            manifest, _ = _read_plan_attempt(project, child.name)
        except ProjectPlanAttemptError:
            continue
        if manifest.get("activated") is True:
            candidates.append(manifest)
    return max(candidates, key=lambda item: str(item.get("created_at", "")), default=None)


def inspect_plan_state(
    project: Project,
    *,
    attempt: str = "latest",
    scope_id: str | None = None,
    issues: bool = False,
    repairs: bool = False,
    segment_id: str | None = None,
    unit_id: str | None = None,
    source_context: int = 0,
) -> dict[str, JsonValue]:
    """Inspect an attempt or the active plan without changing project state."""
    latest_state = _read_latest_plan_attempt(project)
    latest_manifest = latest_state[0] if latest_state is not None else None
    latest_issues = latest_state[1] if latest_state is not None else []
    try:
        active_index = project.load_plan_index() if project.paths["plan_index"].is_file() else None
        active_plans = (
            [(scope, load_scope_plan(project, scope)) for scope in active_index.scopes]
            if active_index is not None
            else []
        )
        if active_index is None:
            active_plan_status = "missing"
        else:
            plan_status = next(
                (
                    row.get("state")
                    for row in semantic_status(project)
                    if row.get("stage") == "plan"
                ),
                "current",
            )
            active_plan_status = str(plan_status)
    except (OSError, UnicodeError, ValueError, InvalidStoredPlanError):
        active_index = None
        active_plans = []
        active_plan_status = "stale"

    selected = "attempt"
    selected_manifest: dict[str, Any] | None = None
    selected_issues: list[dict[str, JsonValue]] = []
    active_view = False
    if attempt == "active":
        active_view = True
        selected = "active_plan"
        selected_manifest = _active_attempt_ref(project)
    elif attempt == "latest":
        if latest_manifest is not None and (
            latest_manifest.get("status") in {"blocked", "incomplete"}
            or latest_manifest.get("activated") is not True
        ):
            selected_manifest = latest_manifest
            selected_issues = latest_issues
        elif active_index is not None:
            active_view = True
            selected = "active_plan"
            selected_manifest = (
                latest_manifest
                if latest_manifest is not None and latest_manifest.get("activated") is True
                else _active_attempt_ref(project)
            )
        else:
            selected_manifest = latest_manifest
            selected_issues = latest_issues
    else:
        selected_manifest, selected_issues = _read_plan_attempt(project, attempt)

    attempt_id = str(selected_manifest["attempt_id"]) if selected_manifest is not None else None
    if active_view:
        scope_rows: list[dict[str, Any]] = []
        for plan_scope, plan in active_plans:
            scope_rows.append(
                {
                    "scope_id": plan_scope.id,
                    "status": "renderable",
                    "source_path": plan_scope.path,
                    "source_sha256": plan_scope.document_sha256,
                    "semantic_planner_fingerprint": plan_scope.semantic_planner_fingerprint,
                    "candidate_path": f"plan/{plan_scope.path}",
                    "issue_count": 0,
                    "repair_count": 0,
                    "reused": False,
                    "plan": plan,
                }
            )
        issue_rows: list[dict[str, JsonValue]] = []
    else:
        scope_rows = list(selected_manifest.get("scopes", [])) if selected_manifest else []
        issue_rows = selected_issues

    if scope_id is not None and not any(
        row.get("scope_id", row.get("id")) == scope_id for row in scope_rows
    ):
        raise ValueError(f"Planning scope {scope_id!r} was not found in the selected view.")
    if scope_id is not None:
        scope_rows = [row for row in scope_rows if row.get("scope_id", row.get("id")) == scope_id]

    plans_by_scope: dict[str, UtterancePlan] = {}
    plans_by_scope_path: dict[str, str | None] = {}
    for row in scope_rows:
        current_scope_id = str(row.get("scope_id", row.get("id", "")))
        plan = row.get("plan") if active_view else None
        if plan is None and attempt_id is not None:
            try:
                plan = _load_attempt_scope_plan(project, attempt_id, row)
            except Exception as exc:
                raise ProjectPlanAttemptError(attempt_id, str(exc)) from exc
        if isinstance(plan, UtterancePlan):
            plans_by_scope[current_scope_id] = plan
        plans_by_scope_path[current_scope_id] = (
            str(row.get("source_path")) if row.get("source_path") is not None else None
        )

    enriched_issues: list[dict[str, JsonValue]] = []
    for issue in issue_rows:
        current_scope_id = str(issue.get("scope_id", ""))
        enriched_issues.append(
            _issue_for_inspection(
                issue,
                scope_id=current_scope_id,
                plan=plans_by_scope.get(current_scope_id),
            )
        )
    if active_view and (issues or repairs):
        for current_scope_id, plan in plans_by_scope.items():
            report = preflight_renderability(plan)
            for issue in report.issues:
                enriched_issues.append(
                    _issue_for_inspection(
                        {
                            **issue.to_dict(),
                            "repair_safe": (
                                issue.repair_assessment.safe
                                if issue.repair_assessment is not None
                                else None
                            ),
                            "repair_action": (
                                issue.repair_assessment.action
                                if issue.repair_assessment is not None
                                else None
                            ),
                            "repair_blockers": (
                                list(issue.repair_assessment.blockers)
                                if issue.repair_assessment is not None
                                else []
                            ),
                        },
                        scope_id=current_scope_id,
                        plan=plan,
                    )
                )

    filtered_issues = [
        issue
        for issue in enriched_issues
        if (segment_id is None or issue.get("segment_id") == segment_id)
        and (unit_id is None or issue.get("unit_id") == unit_id)
    ]
    safe_repairs = [issue for issue in filtered_issues if issue.get("repair_safe") is True]
    segments: list[dict[str, JsonValue]] = []
    if segment_id is not None or unit_id is not None:
        for current_scope_id, plan in plans_by_scope.items():
            for segment in plan.segments:
                member_units = [unit.id for unit in plan.units if segment.id in unit.segment_ids]
                if segment_id is not None and segment.id != segment_id:
                    continue
                if unit_id is not None and unit_id not in member_units:
                    continue
                segments.append(
                    _segment_detail(
                        plan,
                        segment,
                        scope_id=current_scope_id,
                        source_path=plans_by_scope_path.get(current_scope_id),
                        issue_records=filtered_issues,
                        source_context=source_context,
                    )
                )
    scope_output = [
        {
            key: row.get(key)
            for key in (
                "scope_id",
                "status",
                "source_path",
                "source_sha256",
                "semantic_planner_fingerprint",
                "candidate_path",
                "issue_count",
                "repair_count",
                "reused",
                "source_attempt_id",
            )
        }
        for row in scope_rows
    ]
    return cast(
        dict[str, JsonValue],
        json_value(
            {
                "selected": selected,
                "attempt": _attempt_ref(selected_manifest),
                "scopes": scope_output,
                "issues": filtered_issues if issues else [],
                "repairs": safe_repairs if repairs else [],
                "segments": segments,
                "active_plan_status": active_plan_status,
            }
        ),
    )


def _persist_attempt(
    project: Project,
    manifest: dict[str, Any],
    issues: list[dict[str, JsonValue]],
) -> None:
    attempt_id = str(manifest["attempt_id"])
    attempt_dir = _attempt_manifest_path(project, attempt_id).parent
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(
        attempt_dir / "issues.json",
        {
            "schema_version": _PLAN_ATTEMPT_SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "issues": issues,
        },
    )
    atomic_write_json(attempt_dir / "attempt.json", manifest)


def _new_plan_attempt(
    project: Project,
    *,
    renderability_mode: Literal["strict", "repair"],
    settings_sha256: str | None,
    parent_attempt_id: str | None,
) -> dict[str, Any]:
    attempt_id = f"plan-attempt-{secrets.token_hex(8)}"
    attempt_dir = _attempt_manifest_path(project, attempt_id).parent
    attempt_dir.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {
        "schema_version": _PLAN_ATTEMPT_SCHEMA_VERSION,
        "attempt_id": attempt_id,
        "status": "incomplete",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "renderability_mode": renderability_mode,
        "project_planning_settings_sha256": settings_sha256,
        "readio_semantic_planning_version": _READIO_SEMANTIC_PLANNING_VERSION,
        "parent_attempt_id": parent_attempt_id,
        "activated": False,
        "scopes": [],
        "issue_count": 0,
        "repair_count": 0,
    }
    _persist_attempt(project, manifest, [])
    atomic_write_json(
        project.state_path("plan/attempts/latest.json"),
        {"schema_version": _PLAN_ATTEMPT_SCHEMA_VERSION, "attempt_id": attempt_id},
    )
    return manifest


def _scope_source_path(project: Project, scope: DocumentScope) -> Path:
    if project.manifest.schema_version == 4:
        return project.workspace_path(scope.path)
    return project.state_path(scope.path)


def _scope_plan_path(scope: DocumentScope | PlanScope) -> str:
    if scope.id == "document" and scope.kind == "document":
        return "document.utterplan.toml"
    return (Path("chapters") / f"{scope.id}.utterplan.toml").as_posix()


def _scope_source_sha256(project: Project, scope: DocumentScope, document: InputDocument) -> str:
    source_path = _scope_source_path(project, scope)
    if source_path.is_file():
        return hash_file(source_path)
    return sha256_bytes(document.text.encode("utf-8"))


def _attempt_scope_record(
    scope: DocumentScope,
    *,
    source_sha256: str,
    semantic_planner_fingerprint_value: str,
    status: str,
    candidate_path: str | None,
    issue_count: int = 0,
    repair_count: int = 0,
    candidate_sha256: str | None = None,
    plan_id: str | None = None,
    reused: bool = False,
    source_attempt_id: str | None = None,
    candidate_kind: str | None = None,
) -> dict[str, Any]:
    return {
        "scope_id": scope.id,
        "scope_kind": scope.kind,
        "scope_title": scope.title,
        "source_number": scope.source_number,
        "source_path": Path(scope.path).as_posix(),
        "source_sha256": source_sha256,
        "semantic_planner_fingerprint": semantic_planner_fingerprint_value,
        "readio_semantic_planning_version": _READIO_SEMANTIC_PLANNING_VERSION,
        "status": status,
        "candidate_path": candidate_path,
        "candidate_sha256": candidate_sha256,
        "plan_id": plan_id,
        "issue_count": issue_count,
        "repair_count": repair_count,
        "reused": reused,
        "source_attempt_id": source_attempt_id,
        "candidate_kind": candidate_kind,
    }


def _replace_attempt_scope(manifest: dict[str, Any], record: dict[str, Any]) -> None:
    scopes = manifest["scopes"]
    for index, existing in enumerate(scopes):
        if existing.get("scope_id") == record.get("scope_id"):
            scopes[index] = record
            return
    scopes.append(record)


def _load_reusable_scope(
    project: Project,
    prior_attempt: dict[str, Any] | None,
    scope: DocumentScope,
    *,
    source_sha256: str,
    settings_sha256: str | None,
    semantic_fingerprint: str,
    renderability_mode: Literal["strict", "repair"],
) -> tuple[PlannedScope, int] | None:
    if prior_attempt is None:
        return None
    if prior_attempt.get("project_planning_settings_sha256") != settings_sha256:
        return None
    if prior_attempt.get("readio_semantic_planning_version") != _READIO_SEMANTIC_PLANNING_VERSION:
        return None
    rows = prior_attempt.get("scopes")
    if not isinstance(rows, list):
        return None
    record = next(
        (
            item
            for item in rows
            if isinstance(item, dict)
            and item.get("scope_id") == scope.id
            and (
                item.get("status") == "renderable"
                or (renderability_mode == "repair" and item.get("status") == "repaired")
            )
        ),
        None,
    )
    if record is None or any(
        record.get(key) != value
        for key, value in (
            ("source_path", Path(scope.path).as_posix()),
            ("source_sha256", source_sha256),
            ("semantic_planner_fingerprint", semantic_fingerprint),
            ("readio_semantic_planning_version", _READIO_SEMANTIC_PLANNING_VERSION),
        )
    ):
        return None
    candidate_relpath = record.get("candidate_path")
    if not isinstance(candidate_relpath, str):
        return None
    attempt_id = prior_attempt.get("attempt_id")
    if not isinstance(attempt_id, str):
        return None
    try:
        attempt_root = _attempt_manifest_path(project, attempt_id).parent
        candidate_path = project.state_path(
            (Path("plan/attempts") / attempt_id / candidate_relpath).as_posix()
        )
        if attempt_root not in candidate_path.parents or not candidate_path.is_file():
            return None
        serialized = candidate_path.read_bytes()
        digest = sha256_bytes(serialized)
        if digest != record.get("candidate_sha256"):
            return None
        plan = load_current_utterplan(candidate_path)
        if not preflight_renderability(plan).ok:
            return None
        plan = plan.with_identity()
        plan_id = plan.plan_id or ""
        if not plan_id or plan_id != record.get("plan_id"):
            return None
        compiled = CompiledSemanticPlan(plan, plan_id, digest, serialized)
        plan_scope = PlanScope(
            id=scope.id,
            kind=scope.kind,
            path=_scope_plan_path(scope),
            title=scope.title,
            plan_id=plan_id,
            sha256=digest,
            document_sha256=source_sha256,
            semantic_planner_fingerprint=semantic_fingerprint,
        )
        repair_count = record.get("repair_count", 0)
        return PlannedScope(plan_scope, compiled), (
            repair_count if isinstance(repair_count, int) and repair_count >= 0 else 0
        )
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        return None


def _promote_plan_scopes(
    project: Project,
    planned_scopes: list[PlannedScope],
    *,
    settings_sha256: str | None,
) -> None:
    targets: list[tuple[Path, Path, bytes]] = []
    previous_files: dict[Path, bytes | None] = {}
    index_path = project.paths["plan_index"]
    previous_index = index_path.read_bytes() if index_path.is_file() else None
    token = secrets.token_hex(8)
    for planned in planned_scopes:
        target = project.state_path((Path("plan") / planned.scope.path).as_posix())
        staged = target.with_name(f".{target.name}.stage-{token}")
        serialized = planned.compiled.serialized or serialize_utterplan(planned.compiled.plan)
        if not preflight_renderability(planned.compiled.plan).ok:
            raise ValueError(f"refusing to promote non-renderable scope {planned.scope.id}")
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(staged, serialized)
        previous_files[target] = target.read_bytes() if target.is_file() else None
        targets.append((staged, target, serialized))
    index = PlanIndex(
        scopes=tuple(item.scope for item in planned_scopes),
        project_planning_settings_sha256=settings_sha256,
    )
    replaced: list[Path] = []
    try:
        for staged, target, _ in targets:
            os.replace(staged, target)
            replaced.append(target)
        atomic_write_json(index_path, index.to_dict())
    except Exception:
        for target in reversed(replaced):
            previous = previous_files[target]
            if previous is None:
                target.unlink(missing_ok=True)
            else:
                atomic_write_bytes(target, previous)
        if previous_index is None:
            index_path.unlink(missing_ok=True)
        else:
            atomic_write_bytes(index_path, previous_index)
        raise
    finally:
        for staged, _, _ in targets:
            staged.unlink(missing_ok=True)


def plan_project(
    project: Project,
    cfg: Any,
    *,
    on_progress: PlanningProgressCallback | None = None,
    renderability_mode: Literal["strict", "repair"] = "repair",
    parent_attempt_id: str | None = None,
) -> ProjectPlanningResult:
    """Persist a plan attempt incrementally, then promote only a complete valid plan."""
    if project.manifest.schema_version == 4:
        refresh_audiobook_index(project)
    with project_lock(project, operation="plan"):
        document_scopes = project.document_scopes()
        planning_settings = project_settings_from_manifest(
            project.manifest, project.state_root
        ).synthesis
        settings_sha256 = project_planning_settings_fingerprint(planning_settings)
        if parent_attempt_id is None:
            prior_attempt = _load_latest_attempt(project)
            parent_attempt_id = (
                prior_attempt.get("attempt_id") if prior_attempt is not None else None
            )
        else:
            prior_attempt, _ = _read_plan_attempt(project, parent_attempt_id)
        attempt = _new_plan_attempt(
            project,
            renderability_mode=renderability_mode,
            settings_sha256=settings_sha256,
            parent_attempt_id=parent_attempt_id,
        )
        attempt_id = str(attempt["attempt_id"])
        if on_progress is not None:
            on_progress(
                ProjectPlanningProgress(
                    kind="attempt.started",
                    attempt_id=attempt_id,
                    scope_total=len(document_scopes),
                    details={"status": "incomplete", "renderability_mode": renderability_mode},
                )
            )

        planned_scopes: list[PlannedScope] = []
        renderability_issues: list[dict[str, JsonValue]] = []
        reused_scope_ids: list[str] = []
        rebuilt_scope_ids: list[str] = []
        persisted_issues: list[dict[str, JsonValue]] = []
        for index, scope in enumerate(document_scopes, start=1):
            candidate_relpath = f"scopes/scope-{index:04d}.attempt.toml"
            pending = _attempt_scope_record(
                scope,
                source_sha256="",
                semantic_planner_fingerprint_value="",
                status="processing",
                candidate_path=None,
            )
            _replace_attempt_scope(attempt, pending)
            _persist_attempt(project, attempt, persisted_issues)
            if on_progress is not None:
                on_progress(
                    ProjectPlanningProgress(
                        kind="scope.started",
                        attempt_id=attempt_id,
                        scope_id=scope.id,
                        scope_index=index,
                        scope_total=len(document_scopes),
                    )
                )

            source_sha256 = ""
            semantic_fingerprint = ""
            try:
                document = (
                    prepare_project_document(project)
                    if project.manifest.schema_version == 1
                    else project.load_document_scope(scope)
                )
                source_sha256 = _scope_source_sha256(project, scope, document)
                semantic_fingerprint = _planning_fingerprint(cfg, document, renderability_mode)
                processing_record = _attempt_scope_record(
                    scope,
                    source_sha256=source_sha256,
                    semantic_planner_fingerprint_value=semantic_fingerprint,
                    status="processing",
                    candidate_path=None,
                )
                _replace_attempt_scope(attempt, processing_record)
                _persist_attempt(project, attempt, persisted_issues)

                reusable = _load_reusable_scope(
                    project,
                    prior_attempt,
                    scope,
                    source_sha256=source_sha256,
                    settings_sha256=settings_sha256,
                    semantic_fingerprint=semantic_fingerprint,
                    renderability_mode=renderability_mode,
                )
                if reusable is not None:
                    planned, repair_count = reusable
                    candidate_path = project.state_path(
                        (Path("plan/attempts") / attempt_id / candidate_relpath).as_posix()
                    )
                    serialized = planned.compiled.serialized or serialize_utterplan(
                        planned.compiled.plan
                    )
                    atomic_write_bytes(candidate_path, serialized)
                    plan_sha256 = sha256_bytes(serialized)
                    planned = PlannedScope(
                        replace(planned.scope, sha256=plan_sha256),
                        replace(planned.compiled, sha256=plan_sha256, serialized=serialized),
                    )
                    status = "repaired" if repair_count else "renderable"
                    record = _attempt_scope_record(
                        scope,
                        source_sha256=source_sha256,
                        semantic_planner_fingerprint_value=semantic_fingerprint,
                        status=status,
                        candidate_path=candidate_relpath,
                        candidate_sha256=plan_sha256,
                        plan_id=planned.compiled.plan_id,
                        repair_count=repair_count,
                        reused=True,
                        candidate_kind="canonical_plan",
                        source_attempt_id=parent_attempt_id,
                    )
                    _replace_attempt_scope(attempt, record)
                    _persist_attempt(project, attempt, persisted_issues)
                    planned_scopes.append(planned)
                    reused_scope_ids.append(scope.id)
                    if on_progress is not None:
                        on_progress(
                            ProjectPlanningProgress(
                                kind="scope.reused",
                                attempt_id=attempt_id,
                                scope_id=scope.id,
                                scope_index=index,
                                scope_total=len(document_scopes),
                                details={
                                    "status": "saved_attempt",
                                    "candidate_path": candidate_relpath,
                                    "source_attempt_id": parent_attempt_id,
                                },
                            )
                        )
                        on_progress(
                            ProjectPlanningProgress(
                                kind="scope.persisted",
                                attempt_id=attempt_id,
                                scope_id=scope.id,
                                scope_index=index,
                                scope_total=len(document_scopes),
                                details={"status": status, "candidate_path": candidate_relpath},
                            )
                        )
                        on_progress(
                            ProjectPlanningProgress(
                                kind="scope.completed",
                                attempt_id=attempt_id,
                                scope_id=scope.id,
                                scope_index=index,
                                scope_total=len(document_scopes),
                                details={"reused": True},
                            )
                        )
                    continue

                compile_options: dict[str, Any] = {
                    "renderability_mode": renderability_mode,
                }
                if on_progress is not None:
                    compile_options.update(
                        on_progress=on_progress,
                        scope_index=index,
                        scope_total=len(document_scopes),
                    )
                planned = compile_project_scope(project, cfg, scope, document, **compile_options)
            except PlanRenderabilityError as exc:
                enriched = _enrich_renderability_issues(scope, exc.issues)
                renderability_issues.extend(enriched)
                persisted_issues.extend(enriched)
                planning_attempt = getattr(exc, "planning_attempt", None)
                if planning_attempt is not None:
                    diagnostic_relpath = f"scopes/scope-{index:04d}.attempt.toml"
                    candidate_kind = "utterplan_attempt"
                    diagnostic_path = project.state_path(
                        (Path("plan/attempts") / attempt_id / diagnostic_relpath).as_posix()
                    )
                    atomic_write_bytes(
                        diagnostic_path,
                        planning_attempt.to_toml().encode("utf-8"),
                    )
                else:
                    diagnostic_relpath = f"scopes/scope-{index:04d}.attempt.json"
                    candidate_kind = "diagnostic_json"
                    diagnostic_path = project.state_path(
                        (Path("plan/attempts") / attempt_id / diagnostic_relpath).as_posix()
                    )
                    atomic_write_json(
                        diagnostic_path,
                        {
                            "schema_version": _PLAN_ATTEMPT_SCHEMA_VERSION,
                            "attempt_id": attempt_id,
                            "scope_id": scope.id,
                            "status": "blocked",
                            "source_path": Path(scope.path).as_posix(),
                            "source_sha256": source_sha256,
                            "semantic_planner_fingerprint": semantic_fingerprint,
                            "issues": list(enriched),
                        },
                    )
                record = _attempt_scope_record(
                    scope,
                    source_sha256=source_sha256,
                    semantic_planner_fingerprint_value=semantic_fingerprint,
                    status="blocked",
                    candidate_path=diagnostic_relpath,
                    candidate_sha256=hash_file(diagnostic_path),
                    issue_count=len(enriched),
                    candidate_kind=candidate_kind,
                )
                _replace_attempt_scope(attempt, record)
                attempt["issue_count"] = len(persisted_issues)
                _persist_attempt(project, attempt, persisted_issues)
                if on_progress is not None:
                    details: dict[str, JsonValue] = {
                        "mode": renderability_mode,
                        "issue_count": len(exc.issues),
                        "attempt_saved": True,
                        "candidate_path": diagnostic_relpath,
                    }
                    on_progress(
                        ProjectPlanningProgress(
                            kind="renderability.failed",
                            attempt_id=attempt_id,
                            scope_id=scope.id,
                            scope_index=index,
                            scope_total=len(document_scopes),
                            details=details,
                        )
                    )
                    on_progress(
                        ProjectPlanningProgress(
                            kind="scope.failed",
                            attempt_id=attempt_id,
                            scope_id=scope.id,
                            scope_index=index,
                            scope_total=len(document_scopes),
                            details=details,
                        )
                    )
                    on_progress(
                        ProjectPlanningProgress(
                            kind="scope.persisted",
                            attempt_id=attempt_id,
                            scope_id=scope.id,
                            scope_index=index,
                            scope_total=len(document_scopes),
                            details={"status": "blocked", "candidate_path": diagnostic_relpath},
                        )
                    )
                continue
            except Exception as exc:
                failure_record = _attempt_scope_record(
                    scope,
                    source_sha256=source_sha256,
                    semantic_planner_fingerprint_value=semantic_fingerprint,
                    status="failed",
                    candidate_path=None,
                )
                failure_record["failure"] = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
                _replace_attempt_scope(attempt, failure_record)
                attempt["status"] = "incomplete"
                _persist_attempt(project, attempt, persisted_issues)
                if on_progress is not None:
                    on_progress(
                        ProjectPlanningProgress(
                            kind="scope.failed",
                            attempt_id=attempt_id,
                            scope_id=scope.id,
                            scope_index=index,
                            scope_total=len(document_scopes),
                            details={"status": "incomplete", "error_type": type(exc).__name__},
                        )
                    )
                    on_progress(
                        ProjectPlanningProgress(
                            kind="attempt.persisted",
                            attempt_id=attempt_id,
                            scope_index=index,
                            scope_total=len(document_scopes),
                            details={"status": "incomplete"},
                        )
                    )
                raise

            summary = _renderability_summary(planned.compiled.plan)
            repair_count_value = summary["repair_count"]
            repair_count = repair_count_value if isinstance(repair_count_value, int) else 0
            candidate_path = project.state_path(
                (Path("plan/attempts") / attempt_id / candidate_relpath).as_posix()
            )
            candidate_sha256 = _write_plan_artifact(candidate_path, planned.compiled)
            status = "repaired" if repair_count else "renderable"
            record = _attempt_scope_record(
                scope,
                source_sha256=planned.scope.document_sha256 or "",
                semantic_planner_fingerprint_value=(
                    planned.scope.semantic_planner_fingerprint or semantic_fingerprint
                ),
                status=status,
                candidate_path=candidate_relpath,
                candidate_sha256=candidate_sha256,
                plan_id=planned.compiled.plan_id,
                repair_count=repair_count,
                candidate_kind="canonical_plan",
            )
            _replace_attempt_scope(attempt, record)
            _persist_attempt(project, attempt, persisted_issues)
            planned_scopes.append(planned)
            rebuilt_scope_ids.append(scope.id)
            if on_progress is not None:
                on_progress(
                    ProjectPlanningProgress(
                        kind="scope.persisted",
                        attempt_id=attempt_id,
                        scope_id=scope.id,
                        scope_index=index,
                        scope_total=len(document_scopes),
                        details={"status": status, "candidate_path": candidate_relpath},
                    )
                )
                on_progress(
                    ProjectPlanningProgress(
                        kind="scope.completed",
                        attempt_id=attempt_id,
                        scope_id=scope.id,
                        scope_index=index,
                        scope_total=len(document_scopes),
                        details={"reused": False},
                    )
                )

        if renderability_issues:
            attempt["status"] = "blocked"
            attempt["issue_count"] = len(renderability_issues)
            _persist_attempt(project, attempt, persisted_issues)
            if on_progress is not None:
                on_progress(
                    ProjectPlanningProgress(
                        kind="attempt.blocked",
                        attempt_id=attempt_id,
                        scope_total=len(document_scopes),
                        details={"issue_count": len(renderability_issues), "activated": False},
                    )
                )
            error = ProjectPlanRenderabilityError(
                tuple(renderability_issues),
                renderability_mode=renderability_mode,
                attempt_id=attempt_id,
                attempt_status="blocked",
                active_plan_changed=False,
                inspect_command="readio plan inspect .",
                repair_command="readio plan repair .",
            )
            raise error

        renderability_summaries = tuple(
            _renderability_summary(item.compiled.plan) for item in planned_scopes
        )
        renderability_guaranteed = all(
            summary["guaranteed"] is True for summary in renderability_summaries
        )
        repairs = sum(
            summary["repair_count"]
            for summary in renderability_summaries
            if isinstance(summary["repair_count"], int)
        )
        scope_by_id = {scope.id: scope for scope in document_scopes}
        diagnostics = tuple(
            {
                **diagnostic.to_dict(),
                "scope_id": item.scope.id,
                "scope_kind": item.scope.kind,
                "scope_title": item.scope.title,
                "scope_number": scope_by_id[item.scope.id].source_number,
                "source_path": scope_by_id[item.scope.id].path,
            }
            for item in planned_scopes
            for diagnostic in item.compiled.plan.diagnostics
        )
        attempt["status"] = "repaired" if repairs else "renderable"
        attempt["repair_count"] = repairs
        attempt["issue_count"] = 0
        _persist_attempt(project, attempt, persisted_issues)
        if on_progress is not None:
            on_progress(
                ProjectPlanningProgress(
                    kind="attempt.persisted",
                    attempt_id=attempt_id,
                    scope_total=len(document_scopes),
                    details={"status": attempt["status"], "activated": False},
                )
            )
            on_progress(
                ProjectPlanningProgress(
                    kind="attempt.promoting",
                    attempt_id=attempt_id,
                    scope_total=len(document_scopes),
                    details={"scope_count": len(planned_scopes)},
                )
            )
        try:
            _promote_plan_scopes(
                project,
                planned_scopes,
                settings_sha256=settings_sha256,
            )
        except Exception:
            _persist_attempt(project, attempt, persisted_issues)
            raise
        attempt["activated"] = True
        _persist_attempt(project, attempt, persisted_issues)
        if on_progress is not None:
            on_progress(
                ProjectPlanningProgress(
                    kind="attempt.activated",
                    attempt_id=attempt_id,
                    scope_total=len(document_scopes),
                    details={"status": attempt["status"], "activated": True},
                )
            )
        return ProjectPlanningResult(
            scopes=tuple(planned_scopes),
            renderability_mode=renderability_mode,
            renderability_guaranteed=renderability_guaranteed,
            repairs=repairs,
            diagnostics=diagnostics,
            attempt_id=attempt_id,
            activated=True,
            reused_scopes=tuple(reused_scope_ids),
            rebuilt_scopes=tuple(rebuilt_scope_ids),
        )


def repair_plan_project(
    project: Project,
    cfg: Any,
    *,
    attempt_id: str | None = None,
    scope_id: str | None = None,
    dry_run: bool = False,
    on_progress: PlanningProgressCallback | None = None,
) -> dict[str, JsonValue]:
    """Retry a persisted failed attempt using UtterPlan's safe-repair mode."""
    if attempt_id is None:
        latest = _read_latest_plan_attempt(project)
        if latest is None:
            raise ValueError("There is no persisted planning attempt to repair.")
        parent_manifest, _ = latest
        attempt_id = str(parent_manifest["attempt_id"])
    else:
        parent_manifest, _ = _read_plan_attempt(project, attempt_id)
    if parent_manifest.get("activated") is True:
        raise ValueError(f"Planning attempt {attempt_id!r} is already active.")
    if parent_manifest.get("status") not in {"blocked", "incomplete"}:
        raise ValueError(f"Planning attempt {attempt_id!r} is not blocked or incomplete.")

    document_scopes = project.document_scopes()
    scope_ids = {scope.id for scope in document_scopes}
    attempt_scope_ids = {row.get("scope_id") for row in parent_manifest.get("scopes", [])}
    if scope_id is not None and scope_id not in scope_ids:
        raise ValueError(f"Planning scope {scope_id!r} was not found in the project.")
    if scope_id is not None and scope_id not in attempt_scope_ids:
        raise ValueError(f"Planning scope {scope_id!r} was not found in attempt {attempt_id!r}.")

    if dry_run:
        predicted_repairs = 0
        issues: list[dict[str, JsonValue]] = []
        # Activation is project-wide, so dry runs verify every scope even when a scope filter is supplied.
        for index, scope in enumerate(document_scopes, start=1):
            try:
                planned = compile_project_scope(
                    project,
                    cfg,
                    scope,
                    project.load_document_scope(scope),
                    on_progress=on_progress,
                    scope_index=index,
                    scope_total=len(document_scopes),
                    renderability_mode="repair",
                )
                summary = _renderability_summary(planned.compiled.plan)
                count = summary.get("repair_count", 0)
                if isinstance(count, int):
                    predicted_repairs += count
            except PlanRenderabilityError as exc:
                issues.extend(_enrich_renderability_issues(scope, exc.issues))
        return cast(
            dict[str, JsonValue],
            json_value(
                {
                    "attempt": _attempt_ref(parent_manifest),
                    "activated": False,
                    "repairs": predicted_repairs,
                    "reused_scopes": [],
                    "rebuilt_scopes": [],
                    "issues": issues,
                    "dry_run": True,
                    "source_files_changed": False,
                }
            ),
        )

    try:
        result = plan_project(
            project,
            cfg,
            on_progress=on_progress,
            renderability_mode="repair",
            parent_attempt_id=attempt_id,
        )
        new_manifest, issues = _read_plan_attempt(project, result.attempt_id or "")
        return cast(
            dict[str, JsonValue],
            json_value(
                {
                    "attempt": _attempt_ref(new_manifest),
                    "activated": result.activated,
                    "repairs": result.repairs,
                    "reused_scopes": list(result.reused_scopes),
                    "rebuilt_scopes": list(result.rebuilt_scopes),
                    "issues": issues,
                    "dry_run": False,
                    "source_files_changed": False,
                }
            ),
        )
    except ProjectPlanRenderabilityError as exc:
        new_attempt_id = exc.attempt_id
        if new_attempt_id is None:
            raise
        new_manifest, issues = _read_plan_attempt(project, new_attempt_id)
        scope_rows = new_manifest.get("scopes", [])
        return cast(
            dict[str, JsonValue],
            json_value(
                {
                    "attempt": _attempt_ref(new_manifest),
                    "activated": False,
                    "repairs": new_manifest.get("repair_count", 0),
                    "reused_scopes": [
                        row["scope_id"]
                        for row in scope_rows
                        if isinstance(row, Mapping) and row.get("reused") is True
                    ],
                    "rebuilt_scopes": [
                        row["scope_id"]
                        for row in scope_rows
                        if isinstance(row, Mapping) and row.get("reused") is not True
                    ],
                    "issues": issues,
                    "dry_run": False,
                    "source_files_changed": False,
                }
            ),
        )


def plan_document(document: InputDocument, cfg: Any, output: Path) -> CompiledSemanticPlan:
    resolved = resolve_semantic_planning(cfg, document)
    _write_plan_artifact(output, resolved.compiled)
    return resolved.compiled


def load_scope_plan(project: Project, scope: PlanScope) -> UtterancePlan:
    artifact_path = project.state_root / "plan" / scope.path
    try:
        return load_current_utterplan(artifact_path)
    except LegacyPlanArtifactError as error:
        raise InvalidStoredPlanError(
            scope_id=scope.id,
            scope_path=scope.path,
            artifact_path=artifact_path,
            validation_code="legacy_format",
        ) from error
    except PlanSchemaMismatchError as error:
        raise InvalidStoredPlanError(
            scope_id=scope.id,
            scope_path=scope.path,
            artifact_path=artifact_path,
            validation_code="schema_mismatch",
            validation_path="$.schema_version",
        ) from error
    except PlanFormatError as error:
        raise InvalidStoredPlanError(
            scope_id=scope.id,
            scope_path=scope.path,
            artifact_path=artifact_path,
            validation_code=error.code,
            validation_path=error.path,
        ) from error


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
            if scope.path.casefold().endswith(".utterplan.json"):
                return {
                    "state": "stale",
                    "reason": "plan.artifact.legacy_format",
                    "details": {
                        "scope_id": scope.id,
                        "stored_format": "json",
                        "required_format": "toml",
                        "required_schema": SUPPORTED_UTTERPLAN_SCHEMA_VERSION,
                        "action": "run readio plan",
                    },
                }
            plan = load_current_utterplan(path)
        except PlanSchemaMismatchError as exc:
            return {
                "state": "stale",
                "reason": "plan.artifact.schema_mismatch",
                "details": {
                    "scope_id": scope.id,
                    "stored": exc.stored,
                    "required": SUPPORTED_UTTERPLAN_SCHEMA_VERSION,
                    "action": "readio plan build .",
                },
            }
        except PlanFormatError as exc:
            reason = (
                "plan.invalid.not_renderable"
                if exc.code == "segment.not_renderable"
                else "plan.artifact.invalid"
            )
            details: dict[str, Any] = {
                "scope_id": scope.id,
                "validation_code": exc.code,
                "action": "readio plan build .",
            }
            if exc.path:
                details["validation_path"] = exc.path
            return {"state": "invalid", "reason": reason, "details": details}
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
        expected_planner_fingerprint = semantic_planner_fingerprint(plan.config)
        legacy_planner_fingerprint = _legacy_semantic_planner_fingerprint(plan.config)
        if scope.semantic_planner_fingerprint not in {
            expected_planner_fingerprint,
            legacy_planner_fingerprint,
        }:
            return {
                "state": "stale",
                "reason": "plan.stale.planner_version_changed",
                "details": {
                    "scope_id": scope.id,
                    "stored": scope.semantic_planner_fingerprint,
                    "expected": expected_planner_fingerprint,
                    "action": "readio plan build .",
                },
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
    "LegacyPlanArtifactError",
    "PlanSchemaMismatchError",
    "PlannedScope",
    "ProjectPlanningResult",
    "ResolvedSemanticPlanning",
    "compile_project_scope",
    "load_current_utterplan",
    "load_primary_scope_plan",
    "load_scope_plan",
    "plan_document",
    "plan_project",
    "plan_project_scope",
    "prepare_project_document",
    "resolve_semantic_planning",
    "semantic_status",
]
