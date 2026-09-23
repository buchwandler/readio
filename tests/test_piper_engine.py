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
        assert caps.ssmd_voice_binding_mode == "target"
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


class TestPiperAdapterBehavior:
    def test_discover_uses_public_list_voices_and_maps_metadata(self, monkeypatch) -> None:
        from types import SimpleNamespace

        from readio.engines.catalog import CatalogRequest
        from readio.engines.pipersynth import PiperSynthEngineAdapter

        calls = {}
        metadata = SimpleNamespace(
            id="de_DE-thorsten-medium",
            name="Thorsten",
            language_code="de-DE",
            language_family="de",
            region="DE",
            quality="medium",
            num_speakers=2,
            speaker_id_map={"narrator": 0, "announcer": 1},
            aliases=("thorsten",),
            source_revision="rev-1",
        )

        class FakeManager:
            def __init__(self, *, offline):
                calls["offline"] = offline

            def list_voices(self, *, language, quality=None, refresh):
                calls.update(language=language, quality=quality, refresh=refresh)
                return (metadata,)

        monkeypatch.setattr("pipersynth.asset_manager.VoiceAssetManager", FakeManager)
        targets = PiperSynthEngineAdapter().discover(
            CatalogRequest(language="de", offline=True, refresh=True)
        )

        assert calls == {
            "offline": True,
            "language": "de",
            "quality": None,
            "refresh": True,
        }
        assert len(targets) == 1
        target = targets[0]
        assert target.id == metadata.id
        assert target.languages == ("de-DE",)
        assert target.speakers == ("narrator", "announcer")
        assert target.qualities == ("medium",)
        assert target.metadata["source_revision"] == "rev-1"
        assert target.metadata["speaker_id_map"] == {"narrator": 0, "announcer": 1}

    def test_validate_selection_is_catalog_only(self, monkeypatch) -> None:
        from types import SimpleNamespace

        from readio.engines.base import EngineSelection
        from readio.engines.pipersynth import PiperSynthEngineAdapter

        metadata = SimpleNamespace(
            language_code="de-DE",
            speaker_id_map={"narrator": 0},
        )
        calls = {}

        class FakeManager:
            def __init__(self, *, offline):
                calls["offline"] = offline

            def get_voice_metadata(self, voice, *, refresh):
                calls.update(voice=voice, refresh=refresh)
                return metadata

        monkeypatch.setattr("pipersynth.asset_manager.VoiceAssetManager", FakeManager)
        selection = EngineSelection(
            engine="piper",
            target_id="de_DE-thorsten-medium",
            language="de",
            speaker="narrator",
            offline=True,
            refresh=True,
        )
        assert PiperSynthEngineAdapter().validate_selection(selection) == ()
        assert calls == {
            "offline": True,
            "voice": "de_DE-thorsten-medium",
            "refresh": True,
        }

    def test_missing_voice_has_no_synthetic_default(self) -> None:
        import pytest

        from readio.engines.pipersynth import PiperSynthEngineAdapter
        from readio.engines.selection import EngineRequest

        with pytest.raises(ValueError, match="piper.voice_required"):
            PiperSynthEngineAdapter().resolve(EngineRequest(language="de"))

    def test_resolve_filters_generic_options_and_maps_speed(self) -> None:
        from readio.engines.pipersynth import PiperSynthEngineAdapter
        from readio.engines.selection import EngineRequest

        selection, diagnostics = PiperSynthEngineAdapter().resolve(
            EngineRequest(
                engine="piper",
                target_id="voice",
                language="de",
                options={
                    "speed": 2.0,
                    "noise_scale": 0.5,
                    "spacy": "auto",
                    "pause_mode": "auto",
                    "lexicons": None,
                },
                engine_options={"noise_w_scale": 0.25},
            )
        )
        assert diagnostics == ()
        assert selection.options == {
            "length_scale": 0.5,
            "speed": 2.0,
            "noise_scale": 0.5,
            "noise_w_scale": 0.25,
        }
        assert "spacy" not in selection.options
        assert "pause_mode" not in selection.options

    def test_resolve_rejects_invalid_speed(self) -> None:
        import math

        import pytest

        from readio.engines.pipersynth import PiperSynthEngineAdapter
        from readio.engines.selection import EngineRequest

        for speed in (0, -1, math.nan, math.inf):
            with pytest.raises(ValueError, match="piper.invalid_speed"):
                PiperSynthEngineAdapter().resolve(
                    EngineRequest(
                        target_id="voice",
                        language="de",
                        options={"speed": speed},
                    )
                )

    def test_open_uses_pretrained_policy_and_generation(self, monkeypatch) -> None:
        from readio.engines.base import EngineSelection
        from readio.engines.pipersynth import PiperSynthEngineAdapter

        calls = {}

        class FakePipeline:
            def close(self):
                calls["closed"] = True

        def from_pretrained(**kwargs):
            calls.update(kwargs)
            return FakePipeline()

        monkeypatch.setattr("pipersynth.PiperPipeline.from_pretrained", from_pretrained)
        selection = EngineSelection(
            engine="piper",
            target_id="de_DE-thorsten-medium",
            language="de",
            speaker="narrator",
            options={"length_scale": 0.5, "noise_scale": 0.4, "speaker": "narrator"},
            offline=True,
            refresh=True,
        )
        with PiperSynthEngineAdapter().open(selection):
            assert calls["voice"] == "de_DE-thorsten-medium"
            assert calls["language"] == "de"
            assert calls["offline"] is True
            assert calls["refresh_catalog"] is True
            assert calls["generation"].length_scale == 0.5
            assert calls["generation"].speaker == "narrator"
        assert calls["closed"] is True

    def test_v2_piper_plan_is_catalog_only(self, monkeypatch) -> None:
        from types import SimpleNamespace

        from readio.config import ReaderSettings, ReadioConfig
        from readio.document import document_from_text
        from readio.plan import (
            InputRequest,
            OutputRequest,
            PlanRequest,
            SynthesisRequest,
            resolve_execution_v2,
        )

        metadata = SimpleNamespace(
            language_code="de-DE",
            language_family="de",
            region="DE",
            quality="medium",
            num_speakers=1,
            speaker_id_map={"narrator": 0},
            source_revision="rev-1",
        )

        class FakeManager:
            def __init__(self, *, offline):
                pass

            def get_voice_metadata(self, voice, *, refresh):
                return metadata

        monkeypatch.setattr("pipersynth.asset_manager.VoiceAssetManager", FakeManager)
        monkeypatch.setattr(
            "pipersynth.PiperPipeline.from_pretrained",
            lambda **kwargs: (_ for _ in ()).throw(
                AssertionError("Piper runtime must not load during planning")
            ),
        )
        cfg = ReadioConfig(reader=ReaderSettings(engine="piper", voice=None))
        request = PlanRequest(
            operation="render",
            input=InputRequest(document=document_from_text("Hallo Welt")),
            synthesis=SynthesisRequest(
                engine="piper",
                voice="de_DE-thorsten-medium",
                language="de",
            ),
            output=OutputRequest(),
        )
        resolved = resolve_execution_v2(cfg, request)
        assert resolved.plan.ok
        assert resolved.plan.render is not None
        assert resolved.plan.render.engine == "piper"
        assert resolved.plan.render.target.id == "de_DE-thorsten-medium"
        assert resolved.plan.render.target.metadata["source_revision"] == "rev-1"
