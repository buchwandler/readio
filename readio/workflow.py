"""Pure normalization and fail-closed planning for workflow inspections."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from pathlib import Path
from typing import Any

from readio.api.types import (
    WorkflowAction,
    WorkflowArtifactRef,
    WorkflowBlocker,
    WorkflowInspectionState,
    WorkflowPlan,
    WorkflowStage,
    WorkflowStep,
)
from readio.workspace import WorkspacePolicy

_STATE_ALIASES: dict[str, WorkflowInspectionState] = {
    "not_started": "not_started",
    "not_rendered": "not_started",
    "missing": "not_started",
    "partial": "partial",
    "complete": "complete",
    "completed": "complete",
    "reused": "complete",
    "reusable": "complete",
    "written": "complete",
    "valid": "complete",
    "match": "complete",
    "ready": "complete",
    "stale": "stale",
    "blocked": "blocked",
    "not_ready": "blocked",
    "profile_mismatch": "blocked",
    "error": "error",
    "failed": "error",
    "corrupt": "error",
    "invalid": "error",
    "unknown": "unknown",
    "unavailable": "unknown",
}


def _value(report: object, name: str, default: Any = None) -> Any:
    if isinstance(report, Mapping):
        return report.get(name, default)
    return getattr(report, name, default)


def normalize_inspection_state(report: object) -> WorkflowInspectionState:
    """Map specialist-specific labels into the closed Readio inspection vocabulary."""
    raw = _value(report, "state")
    if not isinstance(raw, str):
        return "unknown"
    return _STATE_ALIASES.get(raw.casefold(), "unknown")


def _details(report: object) -> Mapping[str, object]:
    if isinstance(report, Mapping):
        return {str(key): item for key, item in report.items()}
    to_dict = getattr(report, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
        if isinstance(value, Mapping):
            return {str(key): item for key, item in value.items()}
    value = _value(report, "details", {})
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if is_dataclass(report) and not isinstance(report, type):
        return {field.name: getattr(report, field.name) for field in fields(report)}
    return {}


def _reason(report: object, state: WorkflowInspectionState) -> str:
    for name in ("reason", "summary", "detail", "error"):
        value = _value(report, name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if state == "unknown":
        return "The owning specialist did not provide a recognized inspection state."
    return f"The owning specialist reports {state}."


def _blocker(
    code: str,
    message: str,
    package: str,
    *,
    recoverable: bool = True,
    suggested_action: str | None = None,
) -> WorkflowBlocker:
    return WorkflowBlocker(
        code=code,
        message=message,
        owner=package,
        recoverable=recoverable,
        suggested_action=suggested_action,
    )


def plan_workflow_step(
    stage: WorkflowStage,
    package: str,
    inspection: object,
    *,
    selector: str | None = None,
    explicit_rebuild: bool = False,
    replacement_safe: bool = False,
) -> WorkflowStep:
    """Create a fail-closed step with Readio-owned reason and blocker codes."""
    state = normalize_inspection_state(inspection)
    recommended = _value(inspection, "recommended_selector")
    selected = selector
    if isinstance(recommended, str) and recommended.strip():
        selected = recommended
    reason = _reason(inspection, state)
    deferred = _value(inspection, "inspection_deferred") is True
    replacement_required = state == "stale" or _value(inspection, "force_required") is True
    action: WorkflowAction
    reason_code: str
    blockers: tuple[WorkflowBlocker, ...] = ()
    force = False

    if state in {"error", "unknown", "blocked"}:
        action = "blocked"
        if state == "error":
            reason_code = "specialist_error"
            blockers = (_blocker("specialist_error", reason, package, recoverable=False),)
        elif state == "unknown":
            reason_code = "specialist_state_unknown"
            blockers = (
                _blocker(
                    "unknown_specialist_state",
                    reason,
                    package,
                    suggested_action="inspect_specialist_state",
                ),
            )
        elif _value(inspection, "dependency_not_ready") is True:
            reason_code = "dependency_not_ready"
            blockers = (
                _blocker(
                    "dependency_not_ready",
                    reason,
                    package,
                    suggested_action="complete_upstream_stage",
                ),
            )
        else:
            reason_code = "specialist_blocked"
            blockers = (_blocker("specialist_unavailable", reason, package),)
    elif deferred:
        action = "execute"
        reason_code = "inspection_deferred"
    elif replacement_required:
        if stage == "export" and not explicit_rebuild:
            action = "blocked"
            reason_code = "output_replacement_required"
            blockers = (
                _blocker(
                    "output_replace_required",
                    "The final output already exists and requires explicit replacement authorization.",
                    "audioexport",
                    suggested_action="set_replace_output",
                ),
            )
        elif explicit_rebuild and replacement_safe:
            action = "replace"
            reason_code = "explicit_rebuild"
            force = True
        elif explicit_rebuild:
            action = "blocked"
            reason_code = "replacement_not_safe"
            blockers = (
                _blocker(
                    "replacement_not_safe",
                    "The owning specialist did not certify replacement as safe.",
                    package,
                    recoverable=False,
                ),
            )
        elif stage == "export":
            action = "blocked"
            reason_code = "output_replacement_required"
            blockers = (
                _blocker(
                    "output_replace_required",
                    "The final output already exists and requires explicit replacement authorization.",
                    "audioexport",
                    suggested_action="set_replace_output",
                ),
            )
        else:
            action = "blocked"
            reason_code = "replacement_not_authorized"
            blockers = (
                _blocker(
                    "rebuild_required",
                    "An explicit rebuild authorization is required.",
                    package,
                    suggested_action="set_rebuild_from_stage",
                ),
            )
    elif explicit_rebuild and state != "not_started":
        if replacement_safe:
            action = "replace"
            reason_code = "explicit_rebuild"
            force = True
        else:
            action = "blocked"
            reason_code = "replacement_not_safe"
            blockers = (
                _blocker(
                    "replacement_not_safe",
                    "The owning specialist did not certify replacement as safe.",
                    package,
                    recoverable=False,
                ),
            )
    elif state == "complete":
        action = "reuse"
        reason_code = "artifact_current"
    elif state == "stale":
        action = "blocked"
        reason_code = "replacement_not_authorized"
        blockers = (
            _blocker(
                "rebuild_required",
                "An explicit rebuild authorization is required.",
                package,
                suggested_action="set_rebuild_from_stage",
            ),
        )
    elif state == "partial":
        action = "execute"
        reason_code = "artifact_partial"
    elif state == "not_started":
        action = "execute"
        reason_code = "artifact_missing"
    else:
        action = "blocked"
        reason_code = "specialist_state_unknown"
        blockers = (_blocker("unknown_specialist_state", reason, package),)

    if reason_code == "explicit_rebuild":
        reason = f"{reason} Explicit rebuild was requested."
    elif blockers:
        reason = f"{reason} {blockers[0].message}"

    return WorkflowStep(
        stage=stage,
        package=package,
        action=action,
        selector=selected,
        reason=reason,
        force=force,
        state=state,
        details=_details(inspection),
        reason_code=reason_code,  # type: ignore[arg-type]
        blockers=blockers,
    )


def make_workflow_plan(
    source: str | Path,
    policy: WorkspacePolicy,
    *,
    variant: str | None,
    output: str | Path | None,
    steps: tuple[WorkflowStep, ...],
    warnings: tuple[str, ...] = (),
) -> WorkflowPlan:
    """Build the public plan and attach Readio's logical workflow artifact references."""
    source_path = Path(source).expanduser().resolve()
    output_path = Path(output).expanduser().absolute() if output is not None else None
    states: dict[str, WorkflowInspectionState] = {step.stage: step.state for step in steps}
    owners = {
        "source": "ssmdconvert",
        "workspace": "ssmdconvert",
        "compiled_plan": "utterplan",
        "render_plan": "renderplan",
        "rendered_audio": "voicerender",
        "composed_audio": "audiocompose",
        "final_audio": "audioexport",
    }
    artifact_io = {
        "convert": (("source",), ("workspace",)),
        "compile": (("workspace",), ("compiled_plan",)),
        "render_plan": (("compiled_plan",), ("render_plan",)),
        "render": (("render_plan",), ("rendered_audio",)),
        "compose": (("render_plan", "rendered_audio"), ("composed_audio",)),
        "export": (("composed_audio",), ("final_audio",)),
    }

    def artifact(kind: str, state: WorkflowInspectionState | None) -> WorkflowArtifactRef:
        path = source_path if kind == "source" else policy.root if kind == "workspace" else None
        if kind == "final_audio":
            path = output_path
        return WorkflowArtifactRef(kind=kind, owner=owners[kind], path=path, state=state)

    enriched_steps: list[WorkflowStep] = []
    for step in steps:
        input_kinds, output_kinds = artifact_io[step.stage]
        prior_stage: dict[str, str] = {
            "workspace": "convert",
            "compiled_plan": "compile",
            "render_plan": "render_plan",
            "rendered_audio": "render",
            "composed_audio": "compose",
        }
        inputs = tuple(
            artifact(kind, states.get(prior_stage.get(kind, ""))) for kind in input_kinds
        )
        outputs = tuple(artifact(kind, step.state) for kind in output_kinds)
        enriched_steps.append(replace(step, inputs=inputs, outputs=outputs))

    return WorkflowPlan(
        source=source_path,
        workspace=policy.root,
        workspace_mode=policy.mode,
        variant=variant,
        output=output_path,
        steps=tuple(enriched_steps),
        warnings=warnings,
    )
