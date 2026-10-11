"""Semantic planning service for Readio using UtterPlan's public v5 API."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from utterplan import CURRENT_SCHEMA_VERSION, FlowPlan, PlannerProgressEvent, compile_document

from .policy import PlanningPolicy

SUPPORTED_UTTERPLAN_SCHEMA_VERSION = 5
if CURRENT_SCHEMA_VERSION != SUPPORTED_UTTERPLAN_SCHEMA_VERSION:
    raise RuntimeError(
        "Readio requires UtterPlan schema v5; "
        f"installed UtterPlan reports schema {CURRENT_SCHEMA_VERSION}"
    )

logger = logging.getLogger(__name__)


class SemanticPlanningService:
    """Compile prepared text exactly once into a canonical UtterPlan v5 FlowPlan."""

    def __init__(self) -> None:
        self._last_plan: FlowPlan | None = None
        self._last_provenance: dict[str, Any] = {}

    def compile(
        self,
        text: str,
        policy: PlanningPolicy,
        *,
        on_progress: Callable[[PlannerProgressEvent], None] | None = None,
    ) -> FlowPlan:
        """Compile text through UtterPlan's canonical document compiler."""
        result = compile_document(
            text,
            input_format=policy.document_format,
            config=policy.to_planner_config(),
            on_progress=on_progress,
        )
        plan = result.plan
        if plan.schema_version != SUPPORTED_UTTERPLAN_SCHEMA_VERSION:
            raise RuntimeError(
                f"UtterPlan compiler returned schema {plan.schema_version}; "
                f"expected {SUPPORTED_UTTERPLAN_SCHEMA_VERSION}"
            )
        if not plan.plan_id:
            raise ValueError("UtterPlan compiler returned an empty FlowPlan identity")
        self._last_plan = plan
        self._last_provenance = {
            "plan_id": plan.plan_id,
            "schema_version": plan.schema_version,
            "producer": dict(plan.producer),
            "language": plan.language,
            "unit": plan.unit,
            "diagnostics": tuple(result.diagnostics),
        }
        logger.debug(
            "Compiled UtterPlan FlowPlan: plan_id=%s, flows=%d, segments=%d",
            plan.plan_id,
            len(plan.flow),
            sum(len(flow.segments) for flow in plan.flow),
        )
        return plan

    def compile_from_document(
        self,
        document: Any,
        policy: PlanningPolicy,
        *,
        on_progress: Callable[[PlannerProgressEvent], None] | None = None,
    ) -> FlowPlan:
        """Compile the text of an InputDocument."""
        return self.compile(document.text, policy, on_progress=on_progress)

    @property
    def last_plan(self) -> FlowPlan | None:
        return self._last_plan

    @property
    def last_provenance(self) -> dict[str, Any]:
        return self._last_provenance

    def plan_identity(self, plan: FlowPlan) -> str:
        """Return UtterPlan's canonical FlowPlan identity."""
        return plan.plan_id


__all__ = ["SUPPORTED_UTTERPLAN_SCHEMA_VERSION", "FlowPlan", "SemanticPlanningService"]
