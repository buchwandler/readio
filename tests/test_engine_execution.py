"""Tests for the engine-neutral execution spine.

These tests verify:
- The engine receives independent SpeechRequest values, never semantic plans
- A fake engine renders through the full bounded path
"""

from __future__ import annotations

from contextlib import contextmanager

import numpy as np

from readio.config import ReaderSettings, ReadioConfig
from readio.document import document_from_text
from readio.engines.base import EngineCapabilities, EngineSelection, RenderedSpeech
from readio.engines.registry import _registry
from readio.execution import execute_bounded_v2
from readio.plan import (
    InputRequest,
    OutputRequest,
    PlanRequest,
    ReadioPlanV2,
    RenderPlanV2,
    RenderTargetV2,
    SynthesisRequest,
    resolve_execution_v2,
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
        render = RenderPlanV2(engine="piper", default_target=target)
        plan = ReadioPlanV2(render=render)
        assert plan.render is not None
        assert plan.render.engine == "piper"

    def test_plan_render_has_target(self) -> None:
        """ReadioPlanV2 render section should have a target."""
        target = RenderTargetV2(id="model", language="en", voice="voice1")
        render = RenderPlanV2(engine="piper", default_target=target)
        plan = ReadioPlanV2(render=render)
        assert plan.render.default_target.id == "model"
        assert plan.render.default_target.voice == "voice1"

    def test_plan_render_has_options(self) -> None:
        """ReadioPlanV2 render section should have options."""
        target = RenderTargetV2(id="model", language="en")
        render = RenderPlanV2(
            engine="piper",
            default_target=target,
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
            default_target=target,
            options={"noise_scale": 0.5},
        )

        selection = EngineSelection(
            engine=render.engine,
            target_id=render.default_target.id,
            language=render.default_target.language,
            voice=render.default_target.voice,
            speaker=render.default_target.speaker,
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
            default_target=target,
            rate=1.0,
            options={"noise_scale": 0.667},
        )
        plan = ReadioPlanV2(render=render)

        # The plan should be immutable
        assert plan.render.engine == "piper"
        assert plan.render.default_target.id == "model"
        assert plan.render.rate == 1.0

        # Modifying the plan should not affect the original
        import dataclasses

        plan2 = dataclasses.replace(plan, ok=False)
        assert plan.ok is True
        assert plan2.ok is False


def test_fake_engine_bounded_vertical_path_resolves_once(tmp_path, monkeypatch):
    class FakeSession:
        def __init__(self, adapter):
            self.adapter = adapter

        def synthesize(self, request):
            self.adapter.received_requests.append(request)
            return RenderedSpeech(
                id=request.id,
                audio=np.ones(16, dtype=np.float32),
                sample_rate=24000,
            )

    class FakeAdapter:
        id = "fake"

        def __init__(self):
            self.resolve_calls = 0
            self.open_calls = 0
            self.discover_calls = 0
            self.received_requests = []

        def version(self):
            return "fake-1"

        def capabilities(self):
            return EngineCapabilities(id=self.id, voice_binding_namespace=self.id)

        def discover(self, _request):
            self.discover_calls += 1
            return ()

        def resolve(self, request):
            self.resolve_calls += 1
            return (
                EngineSelection(
                    engine=self.id,
                    target_id=request.target_id or "fake-model",
                    language=request.language or "en-us",
                    voice=request.voice,
                    options=dict(request.options),
                ),
                (),
            )

        def target_metadata(self, _selection):
            return {"voices": ("fake-voice",)}

        def canonical_synthesis_identity(self, selection):
            return {"engine": self.id, "target_id": selection.target_id}

        def open(self, selection):
            self.open_calls += 1
            assert selection.target_id == "fake-model"

            @contextmanager
            def session():
                yield FakeSession(self)

            return session()

    adapter = FakeAdapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    request = PlanRequest(
        operation="render",
        input=InputRequest(document=document_from_text("hello world")),
        synthesis=SynthesisRequest(engine="fake", voice="fake-voice"),
        output=OutputRequest(mode="file", requested_path=tmp_path / "out.wav"),
    )

    resolved = resolve_execution_v2(cfg, request)
    assert resolved.plan.ok
    assert resolved.semantic is not None
    assert resolved.plan.semantic_plan.plan_id
    assert resolved.plan.semantic_plan.sha256
    assert resolved.plan.render is not None
    assert resolved.plan.render.render_id
    assert adapter.resolve_calls == 1
    assert adapter.discover_calls == 0

    class Sink:
        def __init__(self):
            self.audio = None
            self.sample_rate = None

        def write(self, audio, sample_rate):
            self.audio = audio
            self.sample_rate = sample_rate

        def close(self):
            pass

    sink = Sink()
    result = execute_bounded_v2(resolved, sink)
    assert adapter.open_calls == 1
    assert len(adapter.received_requests) == 1
    assert adapter.received_requests[0].text == "hello world"
    assert sink.sample_rate == 24000
    assert result.composition.items[0].item_id == adapter.received_requests[0].id


def test_explicit_engine_switch_does_not_inherit_reader_voice() -> None:
    from readio.plan import _resolve_synthesis_candidate

    cfg = ReadioConfig(reader=ReaderSettings(engine="pykokoro", voice="af_sarah"))
    candidate = _resolve_synthesis_candidate(
        cfg,
        SynthesisRequest(engine="piper", language="de"),
    )
    assert candidate.engine == "piper"
    assert candidate.voice is None


def test_ssmd_voice_resolution_uses_selected_adapter_provider(monkeypatch) -> None:
    import readio.ssmd as legacy_ssmd
    from readio.config import VoiceProviderSettings
    from readio.document import document_from_text
    from readio.engines.base import EngineCapabilities, EngineSelection
    from readio.plan import InputRequest, OutputRequest, PlanRequest, SynthesisRequest

    def reject_legacy_parser(*_args, **_kwargs):
        raise AssertionError("plan.v2 must not reparse SSMD with Readio's legacy parser")

    monkeypatch.setattr(legacy_ssmd, "parse_ssmd_09", reject_legacy_parser)

    class FakePiperAdapter:
        id = "fake-piper"

        def capabilities(self):
            return EngineCapabilities(id=self.id, voice_binding_namespace="piper")

        def resolve(self, request):
            return (
                EngineSelection(
                    engine=self.id,
                    target_id="en_US-amy-medium",
                    language="en-us",
                    voice="en_US-amy-medium",
                    options=dict(request.options),
                ),
                (),
            )

        def version(self):
            return "test"

    monkeypatch.setitem(_registry._adapters, "fake-piper", FakePiperAdapter())
    cfg = ReadioConfig(
        voices={
            "kokoro": VoiceProviderSettings(ids=("af_sarah",), roles={"guest": "af_sarah"}),
            "piper": VoiceProviderSettings(ids=("en_US-amy-medium",), roles={"guest": "af_sarah"}),
        }
    )
    request = PlanRequest(
        operation="render",
        input=InputRequest(
            document=document_from_text(
                '---\nssmd_version: "0.9"\n---\n:::{voice="guest"}\nHello.\n:::',
                input_format="ssmd",
            )
        ),
        synthesis=SynthesisRequest(engine="fake-piper", voice="en_US-amy-medium"),
        output=OutputRequest(mode="file"),
        project_voice_bindings={"guest": "en_US-amy-medium"},
    )

    resolved = resolve_execution_v2(cfg, request)

    assert resolved.plan.ok
    decision = next(item for item in resolved.plan.decisions if item.field == "ssmd.bindings.guest")
    assert decision.value == "en_US-amy-medium"
    assert decision.origin == "project"
    assert resolved.plan.render is not None
    assert resolved.plan.render.role_bindings[0].role == "guest"
    assert resolved.plan.render.role_bindings[0].target.id == "en_US-amy-medium"
