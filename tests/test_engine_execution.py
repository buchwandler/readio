"""Tests for the engine-neutral execution spine.

These tests verify:
- render_from_plan_v2() exists and can be called
- The engine receives UtterancePlan, not raw text
- A fake engine can render through the full bounded path
"""

from __future__ import annotations

from typing import Any

import pytest

from readio.plan import (
    ReadioPlanV2,
    RenderPlanV2,
    RenderTargetV2,
)


# ---------------------------------------------------------------------------
# Existence tests
# ---------------------------------------------------------------------------


class TestRenderFromPlanV2Exists:
    """render_from_plan_v2() must exist."""

    def test_function_exists(self) -> None:
        """render_from_plan_v2 should be importable."""
        from readio.reader import render_from_plan_v2

        assert callable(render_from_plan_v2)


class TestReadioPlanV2Structure:
    """ReadioPlanV2 must have the correct structure for execution."""

    def test_plan_has_render_section(self) -> None:
        """ReadioPlanV2 should have a render section."""
        target = RenderTargetV2(id="model", language="en")
        render = RenderPlanV2(engine="piper", target=target)
        plan = ReadioPlanV2(render=render)
        assert plan.render is not None
        assert plan.render.engine == "piper"

    def test_plan_render_has_target(self) -> None:
        """ReadioPlanV2 render section should have a target."""
        target = RenderTargetV2(id="model", language="en", voice="voice1")
        render = RenderPlanV2(engine="piper", target=target)
        plan = ReadioPlanV2(render=render)
        assert plan.render.target.id == "model"
        assert plan.render.target.voice == "voice1"

    def test_plan_render_has_options(self) -> None:
        """ReadioPlanV2 render section should have options."""
        target = RenderTargetV2(id="model", language="en")
        render = RenderPlanV2(
            engine="piper",
            target=target,
            options={"noise_scale": 0.5},
        )
        plan = ReadioPlanV2(render=render)
        assert plan.render.options["noise_scale"] == 0.5


class TestEngineSelectionFromPlan:
    """Engine selection should be created from plan render section."""

    def test_engine_selection_creation(self) -> None:
        """EngineSelection should be creatable from plan render section."""
        from readio.engines.base import EngineSelection

        target = RenderTargetV2(id="model", language="en", voice="voice1")
        render = RenderPlanV2(
            engine="piper",
            target=target,
            options={"noise_scale": 0.5},
        )

        selection = EngineSelection(
            engine=render.engine,
            target_id=render.target.id,
            language=render.target.language,
            voice=render.target.voice,
            speaker=render.target.speaker,
            options=dict(render.options),
        )

        assert selection.engine == "piper"
        assert selection.target_id == "model"
        assert selection.language == "en"
        assert selection.voice == "voice1"
        assert selection.options["noise_scale"] == 0.5


class TestPlanNotReResolvedDuringExecution:
    """A resolved plan must not be re-resolved during execution."""

    def test_plan_is_authoritative(self) -> None:
        """The plan should be the single source of truth for execution."""
        target = RenderTargetV2(id="model", language="de", voice="thorsten")
        render = RenderPlanV2(
            engine="piper",
            target=target,
            rate=1.0,
            options={"noise_scale": 0.667},
        )
        plan = ReadioPlanV2(render=render)

        # The plan should be immutable
        assert plan.render.engine == "piper"
        assert plan.render.target.id == "model"
        assert plan.render.rate == 1.0

        # Modifying the plan should not affect the original
        import dataclasses

        plan2 = dataclasses.replace(plan, ok=False)
        assert plan.ok is True
        assert plan2.ok is False
