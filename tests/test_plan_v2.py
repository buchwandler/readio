"""Tests for readio.plan.v2 schema and resolver.

These tests verify:
- Plan v2 structure
- SemanticPlanRef
- Engine-neutral render section
- resolve_plan_v2() produces correct v2 plan
"""

from __future__ import annotations

from utterplan import CURRENT_SCHEMA_VERSION

from readio.plan import (
    EnvironmentPlanV2,
    PlanningPlanV2,
    ReadioPlanV2,
    RenderPlanV2,
    RenderTargetV2,
    SemanticPlanRef,
    render_identity,
)

# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestSemanticPlanRef:
    """Tests for SemanticPlanRef dataclass."""

    def test_default_values(self) -> None:
        """SemanticPlanRef should have correct default values."""
        ref = SemanticPlanRef()
        assert ref.format == "utterplan"
        assert ref.schema_version == CURRENT_SCHEMA_VERSION == 2
        assert ref.plan_id == ""
        assert ref.sha256 == ""
        assert ref.path is None

    def test_custom_values(self) -> None:
        """SemanticPlanRef should accept custom values."""
        ref = SemanticPlanRef(
            plan_id="test-plan-id",
            sha256="abc123",
            path="/path/to/plan.json",
        )
        assert ref.plan_id == "test-plan-id"
        assert ref.sha256 == "abc123"
        assert ref.path == "/path/to/plan.json"

    def test_to_dict(self) -> None:
        """SemanticPlanRef.to_dict() should return correct dict."""
        ref = SemanticPlanRef(plan_id="test", sha256="hash")
        d = ref.to_dict()
        assert d["format"] == "utterplan"
        assert d["schema_version"] == CURRENT_SCHEMA_VERSION == 2
        assert d["plan_id"] == "test"
        assert d["sha256"] == "hash"
        assert d["path"] is None


class TestRenderTargetV2:
    """Tests for RenderTargetV2 dataclass."""

    def test_basic_target(self) -> None:
        """RenderTargetV2 should store id, language, voice, speaker."""
        target = RenderTargetV2(
            id="test-model",
            language="en-us",
            voice="test-voice",
            speaker=0,
        )
        assert target.id == "test-model"
        assert target.language == "en-us"
        assert target.voice == "test-voice"
        assert target.speaker == 0

    def test_to_dict(self) -> None:
        """RenderTargetV2.to_dict() should omit None values."""
        target = RenderTargetV2(id="model", language="en")
        d = target.to_dict()
        assert d["id"] == "model"
        assert d["language"] == "en"
        assert "voice" not in d
        assert "speaker" not in d


class TestRenderPlanV2:
    """Tests for RenderPlanV2 dataclass."""

    def test_basic_render_plan(self) -> None:
        """RenderPlanV2 should store engine, target, rate, options."""
        target = RenderTargetV2(id="model", language="en")
        render = RenderPlanV2(
            engine="piper",
            target=target,
            rate=1.0,
            options={"noise_scale": 0.5},
        )
        assert render.engine == "piper"
        assert render.target.id == "model"
        assert render.rate == 1.0
        assert render.options["noise_scale"] == 0.5

    def test_to_dict(self) -> None:
        """RenderPlanV2.to_dict() should return correct structure."""
        target = RenderTargetV2(id="model", language="en", voice="voice1")
        render = RenderPlanV2(engine="piper", target=target)
        d = render.to_dict()
        assert d["engine"] == "piper"
        assert d["target"]["id"] == "model"
        assert d["target"]["voice"] == "voice1"
        assert d["rate"] == 1.0


class TestEnvironmentPlanV2:
    """Tests for EnvironmentPlanV2 dataclass."""

    def test_engine_neutral_packages(self) -> None:
        """EnvironmentPlanV2 should use generic package keys."""
        env = EnvironmentPlanV2(
            packages={"readio": "0.1.0", "utterplan": "0.1.2"},
            ffmpeg_available=True,
        )
        assert env.packages["readio"] == "0.1.0"
        assert env.packages["utterplan"] == "0.1.2"
        assert env.ffmpeg_available is True
        # Must NOT have engine-specific fields
        assert not hasattr(env, "pykokoro_version")
        assert not hasattr(env, "piper_version")

    def test_to_dict(self) -> None:
        """EnvironmentPlanV2.to_dict() should return correct structure."""
        env = EnvironmentPlanV2(packages={"readio": "1.0"})
        d = env.to_dict()
        assert d["packages"]["readio"] == "1.0"
        assert "pykokoro_version" not in d


class TestPlanningPlanV2:
    """Tests for PlanningPlanV2 dataclass."""

    def test_basic_planning(self) -> None:
        """PlanningPlanV2 should store planning configuration."""
        planning = PlanningPlanV2(
            language="en-us",
            unit="paragraph",
            pause_mode="auto",
            spacy="auto",
        )
        assert planning.language == "en-us"
        assert planning.unit == "paragraph"
        assert planning.pause_mode == "auto"
        assert planning.spacy == "auto"

    def test_to_dict_omits_none(self) -> None:
        """PlanningPlanV2.to_dict() should omit None values."""
        planning = PlanningPlanV2(language="en", unit="paragraph")
        d = planning.to_dict()
        assert d["language"] == "en"
        assert "text_preparation" not in d
        assert "spacy" not in d


class TestReadioPlanV2:
    """Tests for ReadioPlanV2 dataclass."""

    def test_default_schema(self) -> None:
        """ReadioPlanV2 should have schema='readio.plan.v2'."""
        plan = ReadioPlanV2()
        assert plan.schema == "readio.plan.v2"

    def test_to_dict_structure(self) -> None:
        """ReadioPlanV2.to_dict() should have correct top-level keys."""
        plan = ReadioPlanV2()
        d = plan.to_dict()
        assert "schema" in d
        assert "ok" in d
        assert "operation" in d
        assert "input" in d
        assert "planning" in d
        assert "semantic_plan" in d
        assert "render" in d
        assert "output" in d
        assert "environment" in d
        assert "decisions" in d
        assert "diagnostics" in d

    def test_engine_neutral_render(self) -> None:
        """ReadioPlanV2 render section should be engine-neutral."""
        target = RenderTargetV2(id="model", language="en")
        render = RenderPlanV2(engine="piper", target=target)
        plan = ReadioPlanV2(render=render)
        d = plan.to_dict()
        assert d["render"]["engine"] == "piper"
        assert d["render"]["target"]["id"] == "model"


class TestPykokoroPlanHasNoPiperFields:
    """PyKokoro plans must not have Piper-specific top-level fields."""

    def test_pykokoro_plan_neutral(self) -> None:
        """PyKokoro plan should use engine-neutral structure."""
        target = RenderTargetV2(id="kokoro-model", language="en", voice="kokoro-voice")
        render = RenderPlanV2(engine="pykokoro", target=target)
        plan = ReadioPlanV2(render=render)
        d = plan.to_dict()
        # Should not have Piper-specific fields at top level
        assert "noise_scale" not in d
        assert "length_scale" not in d


class TestPiperPlanHasNoKokoroFields:
    """Piper plans must not have PyKokoro-specific top-level fields."""

    def test_piper_plan_neutral(self) -> None:
        """Piper plan should use engine-neutral structure."""
        target = RenderTargetV2(id="piper-voice", language="de", voice="thorsten")
        render = RenderPlanV2(
            engine="piper",
            target=target,
            options={"noise_scale": 0.667},
        )
        plan = ReadioPlanV2(render=render)
        d = plan.to_dict()
        # Should not have PyKokoro-specific fields at top level
        assert "pykokoro_version" not in d.get("environment", {})
        # Engine-specific options should be in render.options
        assert d["render"]["options"]["noise_scale"] == 0.667


def test_render_identity_is_acoustic_and_packaging_independent():
    semantic_sha = "semantic-sha"
    first = RenderPlanV2(
        engine="piper",
        target=RenderTargetV2(id="voice-a", language="en", voice="voice-a"),
        rate=1.0,
        options={"length_scale": 1.0},
    )
    second = RenderPlanV2(
        engine="piper",
        target=RenderTargetV2(id="voice-a", language="en", voice="voice-a"),
        rate=1.0,
        options={"length_scale": 1.0},
    )
    different_voice = RenderPlanV2(
        engine="piper",
        target=RenderTargetV2(id="voice-b", language="en", voice="voice-b"),
        rate=1.0,
        options={"length_scale": 1.0},
    )
    assert render_identity(semantic_sha, first) == render_identity(semantic_sha, second)
    assert render_identity(semantic_sha, first) != render_identity(semantic_sha, different_voice)
