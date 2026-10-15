"""Semantic plan compiler for Readio.

This module is the compiler boundary that emits canonical UtterPlan v5
``FlowPlan`` artifacts. Plans remain engine-neutral and reusable by the
standalone VoiceRender CLI.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from utterplan import FlowPlan, PlannerProgressEvent

from .semantic import SUPPORTED_UTTERPLAN_SCHEMA_VERSION

if TYPE_CHECKING:
    from ..document import InputDocument
    from .policy import PlanningPolicy

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CompiledSemanticPlan:
    """A canonical UtterPlan v5 plan and its application-side planning metadata."""

    plan: FlowPlan
    plan_id: str
    sha256: str
    planning_config: Mapping[str, Any]
    diagnostics: tuple[Any, ...] = ()
    serialized: bytes | None = None


def _compute_sha256(data: bytes) -> str:
    """Compute SHA-256 hash of the exact canonical plan artifact."""
    return hashlib.sha256(data).hexdigest()


class PlanSchemaMismatchError(ValueError):
    """A persisted Readio plan is not the supported UtterPlan schema."""

    def __init__(self, stored: object) -> None:
        self.stored = stored
        super().__init__(
            f"UtterPlan schema {stored!r} is not supported; "
            f"expected {SUPPORTED_UTTERPLAN_SCHEMA_VERSION}"
        )


class LegacyPlanArtifactError(ValueError):
    """A legacy JSON plan artifact requires explicit source re-planning."""

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"legacy JSON UtterPlan artifact requires re-planning: {path}")


def serialize_utterplan(plan: FlowPlan) -> bytes:
    """Serialize the normal canonical UtterPlan v5 TOML artifact."""
    return plan.to_toml().encode("utf-8")


def load_current_utterplan(path: Path) -> FlowPlan:
    """Load a canonical UtterPlan v5 TOML artifact without schema migration."""
    if path.suffix.lower() == ".json":
        raise LegacyPlanArtifactError(path)
    plan = FlowPlan.load(path)
    plan.validate()
    if plan.schema_version != SUPPORTED_UTTERPLAN_SCHEMA_VERSION:
        raise PlanSchemaMismatchError(plan.schema_version)
    return plan


def compile_semantic_plan(
    document: InputDocument,
    *,
    planning: PlanningPolicy,
    on_progress: Callable[[PlannerProgressEvent], None] | None = None,
) -> CompiledSemanticPlan:
    """Compile a document into one canonical UtterPlan v5 FlowPlan.

    The plan ID is supplied by UtterPlan and represents semantic inputs only;
    Readio configuration is retained separately for project freshness checks.
    """
    from .semantic import SemanticPlanningService

    service = SemanticPlanningService()
    plan = (
        service.compile_from_document(document, planning)
        if on_progress is None
        else service.compile_from_document(document, planning, on_progress=on_progress)
    )
    plan_id = plan.plan_id
    if not plan_id:
        raise ValueError("semantic compiler returned an empty plan identity")

    serialized = serialize_utterplan(plan)
    sha256 = _compute_sha256(serialized)
    planning_config = asdict(planning.to_planner_config())
    planning_config["unit"] = planning.unit
    planning_config["pauses"] = {
        "mode": planning.pause_mode,
        "enabled": planning.pause_enabled,
        "weak": planning.pause_weak,
        "clause": planning.pause_clause,
        "sentence": planning.pause_sentence,
        "paragraph": planning.pause_paragraph,
        "parenthetical": planning.pause_parenthetical,
        "voice_change": planning.pause_voice_change,
    }
    logger.debug(
        "Compiled UtterPlan v5: plan_id=%s, sha256=%s, flows=%d, segments=%d",
        plan_id,
        sha256[:16],
        len(plan.flow),
        sum(len(flow.segments) for flow in plan.flow),
    )
    return CompiledSemanticPlan(
        plan=plan,
        plan_id=plan_id,
        sha256=sha256,
        planning_config=planning_config,
        diagnostics=tuple(service.last_provenance.get("diagnostics", ())),
        serialized=serialized,
    )


__all__ = [
    "CompiledSemanticPlan",
    "FlowPlan",
    "LegacyPlanArtifactError",
    "PlanSchemaMismatchError",
    "compile_semantic_plan",
    "load_current_utterplan",
    "serialize_utterplan",
]
