"""Tests for readio.plan — architectural no-TTS-load and plan/render equivalence."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from readio.config import ReadioConfig
from readio.document import InputDocument
from readio.plan import (
    InputRequest,
    OutputRequest,
    PlanRequest,
    SynthesisRequest,
    resolve_plan,
)
from readio.reader import pipeline_config_from_plan


def _default_config() -> ReadioConfig:
    return ReadioConfig()


def _text_request(
    text: str = "Hello world.",
    *,
    synthesis: SynthesisRequest | None = None,
) -> PlanRequest:
    return PlanRequest(
        operation="render",
        input=InputRequest(
            document=InputDocument(text=text, source_path=None, format="text"),
        ),
        synthesis=synthesis or SynthesisRequest(),
        output=OutputRequest(),
    )


# ---------------------------------------------------------------------------
# No TTS load tests
# ---------------------------------------------------------------------------


class TestNoTTSLoad:
    """Verify that planning does not construct KokoroPipeline or ONNX session."""

    def test_resolve_plan_does_not_import_kokoro_pipeline(self) -> None:
        """resolve_plan should not trigger KokoroPipeline construction."""
        # Patch KokoroPipeline at the pykokoro level
        with patch("pykokoro.KokoroPipeline", side_effect=AssertionError("should not be called")):
            plan = resolve_plan(_default_config(), _text_request())
            assert plan.schema == "readio.plan.v1"

    def test_resolve_plan_does_not_create_onnx_session(self) -> None:
        """resolve_plan should not trigger ONNX session creation."""
        pytest.importorskip("onnxruntime")
        with patch(
            "onnxruntime.InferenceSession",
            side_effect=AssertionError("should not be called"),
        ):
            plan = resolve_plan(_default_config(), _text_request())
            assert plan.schema == "readio.plan.v1"


# ---------------------------------------------------------------------------
# Plan/render equivalence
# ---------------------------------------------------------------------------


class TestPlanRenderEquivalence:
    """Verify that plan values match what pipeline_config_for_document produces."""

    def test_plan_values_match_pipeline_config_for_text(self) -> None:
        """For a text document, plan synthesis values match PipelineConfig."""
        cfg = _default_config()
        request = _text_request(
            synthesis=SynthesisRequest(language="de", model="de-thorsten", model_source="github"),
        )
        plan = resolve_plan(cfg, request)

        assert plan.synthesis is not None
        assert plan.synthesis.model.id == "de-thorsten"
        assert plan.synthesis.model.source == "github"

        # Build pipeline config from plan
        doc = InputDocument(text="Hello world.", source_path=None, format="text")
        pipeline_cfg = pipeline_config_from_plan(plan, doc)

        # Verify they match
        assert pipeline_cfg.model_variant == plan.synthesis.model.id
        assert pipeline_cfg.model_source == plan.synthesis.model.source
        assert pipeline_cfg.model_quality == plan.synthesis.model.quality
        assert pipeline_cfg.voice == plan.synthesis.model.voice
        assert pipeline_cfg.generation.lang == plan.synthesis.language
        assert pipeline_cfg.generation.speed == plan.synthesis.speed
        assert pipeline_cfg.generation.pause_mode == plan.synthesis.pause_mode

    def test_plan_lexicons_match_pipeline_config(self) -> None:
        """Lexicons from plan match PipelineConfig TokenizerConfig."""
        cfg = _default_config()
        request = _text_request(
            synthesis=SynthesisRequest(
                language="de",
                model="de-thorsten",
                model_source="github",
                lexicons=("crane", "gold"),
            ),
        )
        plan = resolve_plan(cfg, request)

        assert plan.synthesis is not None
        assert plan.synthesis.lexicons == ("crane", "gold")

        doc = InputDocument(text="Hello world.", source_path=None, format="text")
        pipeline_cfg = pipeline_config_from_plan(plan, doc)

        assert pipeline_cfg.tokenizer_config is not None
        assert pipeline_cfg.tokenizer_config.lexicons == ("crane", "gold")

    def test_plan_allow_experimental_matches(self) -> None:
        """allow_experimental from plan matches PipelineConfig."""
        cfg = _default_config()
        request = _text_request(
            synthesis=SynthesisRequest(
                language="de",
                model="de-thorsten",
                model_source="github",
                allow_experimental=True,
            ),
        )
        plan = resolve_plan(cfg, request)

        assert plan.synthesis is not None

        doc = InputDocument(text="Hello world.", source_path=None, format="text")
        pipeline_cfg = pipeline_config_from_plan(plan, doc)

        assert pipeline_cfg.allow_experimental_frontend is True


# ---------------------------------------------------------------------------
# Plan deterministic behavior
# ---------------------------------------------------------------------------


class TestPlanDeterminism:
    """Verify that plan resolution is deterministic for the same inputs."""

    def test_same_input_produces_same_synthesis_decisions(self) -> None:
        cfg = _default_config()
        request = _text_request(
            synthesis=SynthesisRequest(language="de", model="de-thorsten", model_source="github"),
        )

        plan1 = resolve_plan(cfg, request)
        plan2 = resolve_plan(cfg, request)

        # Compare synthesis-related decisions (exclude output.path which is random)
        synth_decisions1 = [d for d in plan1.decisions if not d.field.startswith("output.")]
        synth_decisions2 = [d for d in plan2.decisions if not d.field.startswith("output.")]
        assert len(synth_decisions1) == len(synth_decisions2)
        for d1, d2 in zip(synth_decisions1, synth_decisions2):
            assert d1.field == d2.field
            assert d1.value == d2.value
            assert d1.origin == d2.origin

    def test_same_input_produces_same_model_plan(self) -> None:
        cfg = _default_config()
        request = _text_request(
            synthesis=SynthesisRequest(language="de", model="de-thorsten", model_source="github"),
        )

        plan1 = resolve_plan(cfg, request)
        plan2 = resolve_plan(cfg, request)

        assert plan1.synthesis is not None
        assert plan2.synthesis is not None
        assert plan1.synthesis.model.id == plan2.synthesis.model.id
        assert plan1.synthesis.model.source == plan2.synthesis.model.source
        assert plan1.synthesis.model.quality == plan2.synthesis.model.quality
        assert plan1.synthesis.model.voice == plan2.synthesis.model.voice


# ---------------------------------------------------------------------------
# JSON output for invalid plans
# ---------------------------------------------------------------------------


class TestInvalidPlanJson:
    """Verify invalid plans produce valid JSON with diagnostics."""

    def test_invalid_plan_has_diagnostics_in_json(self) -> None:
        cfg = _default_config()
        # Request a non-existent model
        request = _text_request(
            synthesis=SynthesisRequest(model="nonexistent-model-xyz"),
        )
        plan = resolve_plan(cfg, request)

        # Plan should have errors
        assert not plan.ok

        # JSON should be valid and contain diagnostics
        d = plan.to_dict()
        assert d["schema"] == "readio.plan.v1"
        assert d["ok"] is False
        assert len(d["diagnostics"]) > 0

        # Diagnostics should have stable codes
        for diag in d["diagnostics"]:
            assert diag["code"]
            assert diag["severity"] in ("info", "warning", "error")
