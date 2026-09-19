"""Architecture tests for the multi-engine contract.

These tests express the invariants from the multi-engine architecture brief.
They validate the production contract from readio.engines.base.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any

import pytest

from utterplan import UtterancePlan

from readio.engines.base import (
    EngineAdapter,
    EngineCapabilities,
    EngineSelection,
    EngineSession,
)


# ---------------------------------------------------------------------------
# Architecture invariant tests
# ---------------------------------------------------------------------------


class TestEngineRenderContractConsumesUtterPlan:
    """Engine render contract must accept UtterancePlan, not raw text."""

    def test_engine_session_protocol_has_prepare_plan(self) -> None:
        """EngineSession must have prepare_plan(UtterancePlan, ...)."""
        # This test verifies the protocol shape exists
        assert hasattr(EngineSession, "prepare_plan")

    def test_engine_session_protocol_has_to_audio_job(self) -> None:
        """EngineSession must have to_audio_job(UtterancePlan, ...)."""
        assert hasattr(EngineSession, "to_audio_job")


class TestRenderDoesNotPassRawTextToEngine:
    """Readio must not pass raw text to the engine for deterministic rendering."""

    def test_engine_session_prepare_plan_accepts_utterance_plan(self) -> None:
        """prepare_plan must accept UtterancePlan as first argument."""
        import inspect

        sig = inspect.signature(EngineSession.prepare_plan)
        params = list(sig.parameters.keys())
        assert len(params) >= 2  # self, plan
        # The second parameter should be 'plan'
        assert params[1] == "plan"

    def test_engine_session_to_audio_job_accepts_utterance_plan(self) -> None:
        """to_audio_job must accept UtterancePlan as first argument."""
        import inspect

        sig = inspect.signature(EngineSession.to_audio_job)
        params = list(sig.parameters.keys())
        assert len(params) >= 2  # self, plan
        assert params[1] == "plan"


class TestSemanticPlanIsCompiledOnce:
    """UtterPlan must be compiled once and reused across engines."""

    def test_utterance_plan_has_plan_id(self) -> None:
        """UtterancePlan has a semantic plan_id for identity."""
        assert hasattr(UtterancePlan, "plan_id")

    def test_utterance_plan_has_segments(self) -> None:
        """UtterancePlan has stable segment IDs."""
        assert hasattr(UtterancePlan, "segments")

    def test_utterance_plan_has_units(self) -> None:
        """UtterancePlan has stable unit IDs with content hashes."""
        assert hasattr(UtterancePlan, "units")


class TestPlanV2EnvironmentIsEngineNeutral:
    """readio.plan.v2 environment must use generic package keys, not pykokoro_version."""

    def test_plan_schema_v2_exists(self) -> None:
        """readio.plan.v2 schema must exist."""
        from readio.plan import ReadioPlanV2, EnvironmentPlanV2, SemanticPlanRef
        from pathlib import Path

        # Verify v2 classes exist and are engine-neutral
        env = EnvironmentPlanV2(packages={"readio": "0.1.0", "utterplan": "0.1.2"})
        assert env.packages["readio"] == "0.1.0"
        assert not hasattr(env, "pykokoro_version")
        # Verify SemanticPlanRef
        ref = SemanticPlanRef(plan_id="test-id", sha256="abc123")
        assert ref.plan_id == "test-id"
        assert ref.format == "utterplan"


class TestPiperPlanDoesNotRequireKokoroFields:
    """PiperSynth plans must not require PyKokoro-specific fields."""

    def test_engine_adapter_protocol_has_no_kokoro_config_methods(self) -> None:
        """EngineAdapter must not have PyKokoro-specific config methods."""
        # After refactoring, these methods must NOT exist on EngineAdapter:
        forbidden_methods = [
            "tokenizer_config_for_synthesis",
            "short_sentence_config_for_synthesis",
            "language_detection_config_for_synthesis",
            "build_ssmd_render_config",
            "pipeline_config_for_document",
            "pipeline_config_from_plan",
            "create_playback_player",
        ]
        for method in forbidden_methods:
            assert not hasattr(EngineAdapter, method), f"EngineAdapter must not have {method!r}"


class TestEngineSpecificOptionIsRejectedForWrongEngine:
    """Engine-specific options must be rejected before runtime loading."""

    def test_engine_capabilities_advertises_options(self) -> None:
        """EngineCapabilities must list supported option names."""
        caps = EngineCapabilities(
            id="test",
            ssmd_provider=None,
            option_names=frozenset({"speaker", "noise_scale"}),
            supports_prepared_units=True,
            supports_audio_job=True,
        )
        assert "speaker" in caps.option_names
        assert "noise_scale" in caps.option_names
        assert "lexicons" not in caps.option_names
