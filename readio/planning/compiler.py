"""Semantic plan compiler for Readio.

This module provides the explicit compiler boundary that produces one
``UtterancePlan`` from a prepared document and planning policy.  The
compiled plan is engine-neutral and reusable across different engines.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from utterplan import PlannerProgressEvent, UtterancePlan

from .semantic import SUPPORTED_UTTERPLAN_SCHEMA_VERSION

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


class PlanSchemaMismatchError(ValueError):
    """A persisted Readio semantic plan is not the supported UtterPlan schema."""

    def __init__(self, stored: object) -> None:
        self.stored = stored
        super().__init__(
            f"Utterplan schema {stored!r} is not supported; "
            f"expected {SUPPORTED_UTTERPLAN_SCHEMA_VERSION}"
        )


def serialize_utterplan(plan: UtterancePlan) -> bytes:
    """Serialize the canonical UtterPlan artifact with stable JSON fallback."""
    try:
        return plan.to_json().encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        semantic = plan.semantic_dict()
        return json.dumps(
            semantic, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")


def load_utterplan_v3(path: Path) -> UtterancePlan:
    """Load an Utterplan v3 artifact without invoking schema migration."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("format") != "utterplan":
        raise ValueError("semantic plan artifact is not an Utterplan document")
    stored = data.get("schema_version")
    if type(stored) is not int or stored != SUPPORTED_UTTERPLAN_SCHEMA_VERSION:
        raise PlanSchemaMismatchError(stored)
    return UtterancePlan.from_dict(data)


def compile_semantic_plan(
    document: InputDocument,
    *,
    planning: PlanningPolicy,
    on_progress: Callable[[PlannerProgressEvent], None] | None = None,
) -> CompiledSemanticPlan:
    """Compile a document into a semantic UtterancePlan.

    This is the single compiler boundary.  It produces one immutable
    ``UtterancePlan`` from the prepared document and planning policy.
    The plan identity is stable for the same semantic inputs and does
    NOT change when acoustic choices (engine, model, voice, etc.) change.

    Args:
        document: The prepared input document.
        planning: Planning policy (language, unit, pause mode, etc.).

    Returns:
        A ``CompiledSemanticPlan`` with the plan, plan_id, and sha256.
    """
    from .semantic import SemanticPlanningService

    service = SemanticPlanningService()
    plan = (
        service.compile_from_document(document, planning)
        if on_progress is None
        else service.compile_from_document(document, planning, on_progress=on_progress)
    )
    # Compute identity after the compiler has materialized UtterPlan identity.
    plan = plan.with_identity()
    plan_id = plan.plan_id or ""
    if not plan_id:
        raise ValueError("semantic compiler returned an empty plan identity")

    # Compute SHA-256 of the exact canonical serialized artifact.
    serialized = serialize_utterplan(plan)
    sha256 = _compute_sha256(serialized)
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


__all__ = [
    "CompiledSemanticPlan",
    "PlanSchemaMismatchError",
    "UtterancePlan",
    "compile_semantic_plan",
    "load_utterplan_v3",
    "serialize_utterplan",
]
