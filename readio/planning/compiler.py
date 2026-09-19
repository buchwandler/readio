"""Semantic plan compiler for Readio.

This module provides the explicit compiler boundary that produces one
``UtterancePlan`` from a prepared document and planning policy.  The
compiled plan is engine-neutral and reusable across different engines.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from utterplan import UtterancePlan

if TYPE_CHECKING:
    from ..document import InputDocument
    from .policy import PlanningPolicy

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CompiledSemanticPlan:
    """A compiled semantic plan with identity information."""

    plan: UtterancePlan
    plan_id: str
    sha256: str
    serialized: bytes | None = None


def _compute_sha256(data: bytes) -> str:
    """Compute SHA-256 hash of data."""
    return hashlib.sha256(data).hexdigest()


def compile_semantic_plan(
    document: InputDocument,
    *,
    planning: PlanningPolicy,
    engine_config: Any = None,
) -> CompiledSemanticPlan:
    """Compile a document into a semantic UtterancePlan.

    This is the single compiler boundary.  It produces one immutable
    ``UtterancePlan`` from the prepared document and planning policy.
    The plan identity is stable for the same semantic inputs and does
    NOT change when acoustic choices (engine, model, voice, etc.) change.

    Args:
        document: The prepared input document.
        planning: Planning policy (language, unit, pause mode, etc.).
        engine_config: Engine-specific planner configuration.

    Returns:
        A ``CompiledSemanticPlan`` with the plan, plan_id, and sha256.
    """
    from .semantic import SemanticPlanningService

    service = SemanticPlanningService()
    plan = service.compile_from_document(document, planning, engine_config)

    # Compute identity
    plan_id = plan.plan_id or ""

    # Compute SHA-256 of the canonical serialized form
    try:
        serialized = plan.to_json().encode("utf-8")
        sha256 = _compute_sha256(serialized)
    except Exception:
        # Fallback: use semantic dict hash
        semantic = plan.semantic_dict()
        sha256 = _compute_sha256(json.dumps(semantic, sort_keys=True).encode("utf-8"))
        serialized = None

    logger.debug(
        "Compiled semantic plan: plan_id=%s, sha256=%s, segments=%d, units=%d",
        plan_id,
        sha256[:16],
        len(plan.segments),
        len(plan.units),
    )

    return CompiledSemanticPlan(
        plan=plan,
        plan_id=plan_id,
        sha256=sha256,
        serialized=serialized,
    )


__all__ = ["CompiledSemanticPlan", "compile_semantic_plan"]
