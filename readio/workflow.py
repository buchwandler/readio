"""Pure normalization and fail-closed planning for workflow inspections."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

from readio.api.types import (
    WorkflowAction,
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


def plan_workflow_step(
    stage: WorkflowStage,
    package: str,
    inspection: object,
    *,
    selector: str | None = None,
    explicit_rebuild: bool = False,
    replacement_safe: bool = False,
) -> WorkflowStep:
    """Create a step without turning unknown or unsafe state into destructive work.

    ``replacement_safe`` must come from the owning specialist's inspection; Readio
    never infers safety from an output path or file's existence.
    """
    state = normalize_inspection_state(inspection)
    recommended = _value(inspection, "recommended_selector")
    selected = selector
    if isinstance(recommended, str) and recommended.strip():
        selected = recommended
    reason = _reason(inspection, state)
    replacement_required = state == "stale" or _value(inspection, "force_required") is True
    action: WorkflowAction
    force = False
    if state in {"error", "unknown", "blocked"}:
        action = "blocked"
        reason = f"{reason} Automatic convergence fails closed for this state."
    elif replacement_required:
        if explicit_rebuild and replacement_safe:
            action = "replace"
            force = True
        else:
            action = "blocked"
            reason = (
                f"{reason} Explicit rebuild and specialist-certified replacement safety are "
                "both required."
            )
    elif explicit_rebuild and state != "not_started":
        if replacement_safe:
            action = "replace"
            force = True
        else:
            action = "blocked"
            reason = f"{reason} Explicit rebuild requires specialist-certified replacement safety."
    elif state == "complete":
        action = "reuse"
    elif state in {"not_started", "partial"}:
        action = "execute"
    else:
        action = "blocked"
    return WorkflowStep(
        stage=stage,
        package=package,
        action=action,
        selector=selected,
        reason=reason,
        force=force,
        state=state,
        details=_details(inspection),
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
    """Build the public plan envelope from resolved workspace policy and stage steps."""
    return WorkflowPlan(
        source=Path(source).expanduser().resolve(),
        workspace=policy.root,
        workspace_mode=policy.mode,
        variant=variant,
        output=Path(output).expanduser().absolute() if output is not None else None,
        steps=steps,
        warnings=warnings,
    )
