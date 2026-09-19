"""Semantic planning service for Readio.

This module provides the SemanticPlanningService that compiles UtterancePlans
from engine-specific planner configurations, ensuring exactly one plan is
created per render.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from utterplan import UtterancePlan, UtterancePlanner

from .policy import PlanningPolicy

logger = logging.getLogger(__name__)


class SemanticPlanningService:
    """Service for compiling semantic UtterancePlans.

    This service:
    1. Gets planner requirements from the engine adapter
    2. Combines them with Readio document/config policy
    3. Invokes UtterancePlanner
    4. Returns an immutable UtterancePlan
    5. Records plan ID and provenance
    """

    def __init__(self) -> None:
        self._last_plan: UtterancePlan | None = None
        self._last_provenance: dict[str, Any] = {}

    def compile(
        self,
        text: str,
        policy: PlanningPolicy,
        engine_config: Any = None,
        *,
        source_format: str = "text",
        source_path: str | None = None,
        document_metadata: dict[str, Any] | None = None,
    ) -> UtterancePlan:
        """Compile text into an UtterancePlan.

        Args:
            text: The text to plan.
            policy: Planning policy combining engine and Readio config.
            engine_config: Engine-specific planner configuration.
            source_format: Format of the source text.
            source_path: Path to the source file, if any.
            document_metadata: Additional document metadata.

        Returns:
            Immutable UtterancePlan with plan_id and provenance.
        """
        from utterplan import PlannerConfig

        # Get combined planner config as dict
        config_dict = policy.to_planner_config(engine_config)

        # Create PlannerConfig object
        planner_config = PlannerConfig(
            language=config_dict.get("language", "en-us"),
            document_format=config_dict.get("document_format", "plain"),
            text_preparation=config_dict.get("text_preparation", "identity"),
            unit=config_dict.get("unit", "paragraph"),
        )

        # Create planner
        planner = UtterancePlanner(planner_config)

        # Compile the plan
        plan = planner.plan(
            text,
            config=planner_config,
            unit=policy.unit,
        )

        # Ensure plan has identity
        plan = plan.with_identity()

        # Record provenance
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

    def compile_from_document(
        self,
        document: Any,
        policy: PlanningPolicy,
        engine_config: Any = None,
    ) -> UtterancePlan:
        """Compile from an InputDocument.

        Args:
            document: InputDocument with text and metadata.
            policy: Planning policy.
            engine_config: Engine-specific planner configuration.

        Returns:
            Immutable UtterancePlan.
        """
        text = document.text
        source_format = document.format if hasattr(document, "format") else "text"
        source_path = document.source_path if hasattr(document, "source_path") else None
        document_metadata = document.metadata if hasattr(document, "metadata") else {}

        return self.compile(
            text=text,
            policy=policy,
            engine_config=engine_config,
            source_format=source_format,
            source_path=source_path,
            document_metadata=document_metadata,
        )

    @property
    def last_plan(self) -> UtterancePlan | None:
        """Return the last compiled plan."""
        return self._last_plan

    @property
    def last_provenance(self) -> dict[str, Any]:
        """Return provenance of the last compiled plan."""
        return self._last_provenance

    def plan_identity(self, plan: UtterancePlan) -> str:
        """Compute a stable identity for a plan.

        This uses the plan's semantic hash, not just the plan_id.
        """
        from utterplan import semantic_hash

        return semantic_hash(plan.semantic_dict())


__all__ = ["SemanticPlanningService"]
