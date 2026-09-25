"""Semantic planning service for Readio."""

from __future__ import annotations

import logging
from typing import Any

from utterplan import UtterancePlan, UtterancePlanner

from .policy import PlanningPolicy

logger = logging.getLogger(__name__)


class SemanticPlanningService:
    """Compile prepared text exactly once using Readio's typed policy."""

    def __init__(self) -> None:
        self._last_plan: UtterancePlan | None = None
        self._last_provenance: dict[str, Any] = {}

    def compile(self, text: str, policy: PlanningPolicy) -> UtterancePlan:
        """Compile text into the canonical UtterancePlan."""
        planner = UtterancePlanner(policy.to_planner_config())
        plan = planner.plan(text)
        self._last_plan = plan
        self._last_provenance = {
            "plan_id": plan.plan_id,
            "schema_version": plan.schema_version,
            "producer": dict(plan.producer),
            "language": policy.language,
            "unit": policy.unit,
        }
        logger.debug(
            "Compiled UtterancePlan: plan_id=%s, segments=%d, units=%d",
            plan.plan_id,
            len(plan.segments),
            len(plan.units),
        )
        return plan

    def compile_from_document(self, document: Any, policy: PlanningPolicy) -> UtterancePlan:
        """Compile the text of an InputDocument."""
        return self.compile(document.text, policy)

    @property
    def last_plan(self) -> UtterancePlan | None:
        return self._last_plan

    @property
    def last_provenance(self) -> dict[str, Any]:
        return self._last_provenance

    def plan_identity(self, plan: UtterancePlan) -> str:
        """Return UtterPlan's canonical semantic identity."""
        return plan.plan_id


__all__ = ["SemanticPlanningService"]
