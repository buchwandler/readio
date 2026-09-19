"""Tests for the Piper engine adapter.

These tests verify:
- Piper can be selected as canonical --engine piper
- PiperSynthEngineAdapter is reachable through the registry
- Piper-specific options are validated
- Piper plan does not import pykokoro
"""

from __future__ import annotations

from readio.engines.registry import normalize_engine_id

# ---------------------------------------------------------------------------
# Canonical ID tests
# ---------------------------------------------------------------------------


class TestPiperCanonicalId:
    """Piper must use canonical engine ID 'piper'."""

    def test_piper_is_canonical(self) -> None:
        """'piper' should be a canonical engine ID."""
        assert normalize_engine_id("piper") == "piper"

    def test_pipersynth_normalizes_to_piper(self) -> None:
        """'pipersynth' should normalize to 'piper'."""
        assert normalize_engine_id("pipersynth") == "piper"


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestPiperInRegistry:
    """Piper should be discoverable through the engine registry."""

    def test_piper_in_canonical_ids(self) -> None:
        """'piper' should be in CANONICAL_ENGINE_IDS."""
        from readio.engines.registry import CANONICAL_ENGINE_IDS

        assert "piper" in CANONICAL_ENGINE_IDS

    def test_piper_adapter_class_exists(self) -> None:
        """PiperSynthEngineAdapter class should exist."""
        from readio.engines.pipersynth import PiperSynthEngineAdapter

        assert PiperSynthEngineAdapter is not None

    def test_piper_adapter_id(self) -> None:
        """PiperSynthEngineAdapter should have id='piper'."""
        from readio.engines.pipersynth import PiperSynthEngineAdapter

        adapter = PiperSynthEngineAdapter()
        assert adapter.id == "piper"


# ---------------------------------------------------------------------------
# Capabilities tests
# ---------------------------------------------------------------------------


class TestPiperCapabilities:
    """Piper adapter should advertise correct capabilities."""

    def test_piper_capabilities(self) -> None:
        """Piper adapter should have correct capabilities."""
        from readio.engines.pipersynth import PiperSynthEngineAdapter

        adapter = PiperSynthEngineAdapter()
        caps = adapter.capabilities()

        assert caps.id == "piper"
        assert caps.ssmd_provider == "piper"
        assert caps.supports_lexicons is False
        assert caps.supports_speakers is True

    def test_piper_option_names(self) -> None:
        """Piper adapter should advertise Piper-specific options."""
        from readio.engines.pipersynth import PiperSynthEngineAdapter

        adapter = PiperSynthEngineAdapter()
        caps = adapter.capabilities()

        # Piper-specific options
        assert "noise_scale" in caps.option_names
        assert "length_scale" in caps.option_names
        assert "speaker" in caps.option_names


# ---------------------------------------------------------------------------
# Plan structure tests
# ---------------------------------------------------------------------------


class TestPiperPlanStructure:
    """Piper plans should use engine-neutral structure."""

    def test_piper_render_plan(self) -> None:
        """Piper render plan should have engine='piper'."""
        from readio.plan import RenderPlanV2, RenderTargetV2

        target = RenderTargetV2(id="de_DE-thorsten-medium", language="de", voice="thorsten")
        render = RenderPlanV2(
            engine="piper",
            target=target,
            rate=1.0,
            options={"noise_scale": 0.667, "length_scale": 1.0},
        )

        assert render.engine == "piper"
        assert render.target.id == "de_DE-thorsten-medium"
        assert render.options["noise_scale"] == 0.667

    def test_piper_plan_no_kokoro_fields(self) -> None:
        """Piper plan should not have PyKokoro-specific fields at top level."""
        from readio.plan import ReadioPlanV2, RenderPlanV2, RenderTargetV2

        target = RenderTargetV2(id="voice", language="de")
        render = RenderPlanV2(engine="piper", target=target)
        plan = ReadioPlanV2(render=render)
        d = plan.to_dict()

        # Should not have PyKokoro-specific environment fields
        assert "pykokoro_version" not in d.get("environment", {})
        # Should not have PyKokoro-specific synthesis fields
        assert "model" not in d.get("render", {})


# ---------------------------------------------------------------------------
# Option validation tests
# ---------------------------------------------------------------------------


class TestPiperOptionValidation:
    """Piper-specific options should be validated before runtime loading."""

    def test_piper_rejects_lexicon_option(self) -> None:
        """Piper should reject lexicon options as unsupported."""
        from readio.engines.pipersynth import PiperSynthEngineAdapter

        adapter = PiperSynthEngineAdapter()
        caps = adapter.capabilities()

        # Lexicons should not be supported
        assert caps.supports_lexicons is False
        assert "lexicons" not in caps.option_names
