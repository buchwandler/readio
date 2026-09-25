from __future__ import annotations

from readio.config import ReaderSettings, ReadioConfig
from readio.document import document_from_text
from readio.engines import EngineCapabilities, EngineSelection
from readio.engines.registry import _registry
from readio.plan import (
    InputRequest,
    OutputRequest,
    PlanRequest,
    SynthesisRequest,
    resolve_execution_v2,
)


class _PlanningOnlyAdapter:
    id = "planning-only"

    def version(self):
        return "1.0"

    def capabilities(self):
        return EngineCapabilities(id=self.id, voice_binding_namespace=self.id)

    def resolve(self, request):
        return (
            EngineSelection(
                engine=self.id,
                target_id="fixture-model",
                language=request.language or "en-us",
                voice=request.voice,
                options=dict(request.options),
            ),
            (),
        )

    def target_metadata(self, _selection):
        return {"languages": ("en-us",), "voices": ("fixture-voice",)}

    def canonical_synthesis_identity(self, selection):
        return {"engine": self.id, "target_id": selection.target_id}

    def open(self, _selection):
        raise AssertionError("planning must not open a synthesis runtime")


def test_plan_v2_compiles_semantics_without_opening_a_synthesis_runtime(monkeypatch):
    adapter = _PlanningOnlyAdapter()
    monkeypatch.setitem(_registry._adapters, adapter.id, adapter)
    request = PlanRequest(
        operation="render",
        input=InputRequest(document=document_from_text("Plan without loading TTS.")),
        synthesis=SynthesisRequest(engine=adapter.id, voice="fixture-voice"),
        output=OutputRequest(),
    )

    resolved = resolve_execution_v2(
        ReadioConfig(reader=ReaderSettings(engine=adapter.id, spacy="off")),
        request,
    )

    assert resolved.plan.ok
    assert resolved.plan.schema == "readio.plan.v2"
    assert resolved.semantic is not None
    assert resolved.semantic.plan.units
    assert resolved.plan.render.default_target.id == "fixture-model"
