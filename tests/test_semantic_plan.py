"""Tests for semantic plan compilation and identity.

These tests verify:
- Semantic plan identity is stable for same input
- Identity changes when semantically relevant inputs change
- Identity does NOT change for acoustic choices
"""

from __future__ import annotations

from typing import Any

from readio.document import InputDocument
from readio.planning.compiler import CompiledSemanticPlan, compile_semantic_plan
from readio.planning.policy import PlanningPolicy

# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------


def _make_document(text: str, language: str = "en-us") -> InputDocument:
    """Create a simple InputDocument for testing."""
    return InputDocument(text=text, source_path=None, format="text")


def _make_policy(language: str = "en-us", unit: str = "paragraph", **kwargs: Any) -> PlanningPolicy:
    """Create a simple PlanningPolicy for testing."""
    return PlanningPolicy(
        language=language,
        unit=unit,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Identity stability tests
# ---------------------------------------------------------------------------


class TestSemanticPlanIdentityStable:
    """Semantic plan identity must be stable for the same input."""

    def test_same_text_same_identity(self) -> None:
        """Same text produces the same plan_id and sha256."""
        doc = _make_document("Hello world, this is a test.")
        policy = _make_policy()

        plan1 = compile_semantic_plan(doc, planning=policy)
        plan2 = compile_semantic_plan(doc, planning=policy)

        assert plan1.plan_id == plan2.plan_id
        assert plan1.sha256 == plan2.sha256

    def test_plan_has_plan_id(self) -> None:
        """Compiled plan must have a plan_id."""
        doc = _make_document("Test text for identity.")
        policy = _make_policy()

        result = compile_semantic_plan(doc, planning=policy)

        assert result.plan_id is not None
        assert len(result.plan_id) > 0

    def test_plan_has_sha256(self) -> None:
        """Compiled plan must have a sha256 hash."""
        doc = _make_document("Test text for hash.")
        policy = _make_policy()

        result = compile_semantic_plan(doc, planning=policy)

        assert result.sha256 is not None
        assert len(result.sha256) == 64  # SHA-256 hex digest

    def test_plan_is_compiled_semantic_plan(self) -> None:
        """Result must be a CompiledSemanticPlan instance."""
        doc = _make_document("Test.")
        policy = _make_policy()

        result = compile_semantic_plan(doc, planning=policy)

        assert isinstance(result, CompiledSemanticPlan)
        assert result.plan is not None


class TestSemanticPlanIdentityChanges:
    """Identity must change when semantically relevant inputs change."""

    def test_identity_changes_when_text_changes(self) -> None:
        """Different text produces different identity."""
        policy = _make_policy()

        plan1 = compile_semantic_plan(_make_document("Hello world"), planning=policy)
        plan2 = compile_semantic_plan(_make_document("Goodbye world"), planning=policy)

        assert plan1.sha256 != plan2.sha256

    def test_identity_changes_when_language_changes(self) -> None:
        """Different language produces different identity."""
        doc = _make_document("Hello world")

        plan_en = compile_semantic_plan(doc, planning=_make_policy(language="en-us"))
        plan_de = compile_semantic_plan(doc, planning=_make_policy(language="de"))

        assert plan_en.sha256 != plan_de.sha256


class TestSemanticPlanIdentityAcousticInvariant:
    """Identity must NOT change for acoustic choices.

    The semantic plan is engine-neutral.  Changing engine, model, voice,
    speaker, output format, etc. must not change the plan identity.
    """

    def test_engine_config_cannot_override_semantics(self) -> None:
        """Engine-specific planner configuration is rejected explicitly."""
        import pytest

        doc = _make_document("Hello world, this is a test.")
        with pytest.raises(ValueError, match="engine-specific planner configuration"):
            compile_semantic_plan(
                doc, planning=_make_policy(), engine_config={"voice": "different"}
            )


def test_semantic_compiler_uses_typed_planner_config() -> None:
    from utterplan.config import SSMDConfig

    policy = _make_policy(
        spacy_policy="off",
        pause_mode="manual",
        ssmd=SSMDConfig(unknown_header="ignore"),
        overlap_mode="strict",
        language_aliases={"en": "en-us"},
        diagnostics=False,
    )
    result = compile_semantic_plan(_make_document("Hello world"), planning=policy)
    config = result.plan.semantic_dict()["config"]
    assert config["pauses"]["mode"] == "manual"
    assert config["linguistics"]["use_spacy"] is False
    assert config["ssmd"]["unknown_header"] == "ignore"
    assert config["overlap_mode"] == "strict"
    assert config["language_aliases"] == {"en": "en-us"}
