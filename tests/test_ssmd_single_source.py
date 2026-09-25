from __future__ import annotations

from readio.config import ReaderSettings, ReadioConfig, VoiceProviderSettings
from readio.document import InputDocument
from readio.engines import EngineCapabilities, EngineSelection
from readio.engines.registry import _registry
from readio.plan import (
    InputRequest,
    OutputRequest,
    PlanRequest,
    SynthesisRequest,
    resolve_execution_v2,
)


class _FixtureAdapter:
    id = "fixture"

    def version(self):
        return "1.0"

    def capabilities(self):
        return EngineCapabilities(
            id=self.id,
            voice_binding_namespace="kokoro",
            supports_named_voices=True,
        )

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
        return {"voices": ("af_sarah", "af_bella")}

    def canonical_synthesis_identity(self, selection):
        return {"engine": self.id, "target_id": selection.target_id}


def test_ssmd_role_binding_comes_from_compiled_semantic_metadata(monkeypatch):
    adapter = _FixtureAdapter()
    monkeypatch.setitem(_registry._adapters, adapter.id, adapter)
    config = ReadioConfig(
        reader=ReaderSettings(engine=adapter.id, voice="af_sarah", spacy="off"),
        voices={
            "kokoro": VoiceProviderSettings(
                ids=("af_sarah", "af_bella"),
                roles={"host": "af_sarah"},
            )
        },
    )
    document = InputDocument(
        text=(
            "---\nssmd_version: '0.9'\nvoice_bindings:\n"
            "  kokoro:\n    host: af_bella\n---\n\n"
            ':::{voice="host"}\nHello from the host.\n:::\n'
        ),
        source_path=None,
        format="ssmd",
    )
    request = PlanRequest(
        operation="render",
        input=InputRequest(document=document),
        synthesis=SynthesisRequest(engine=adapter.id),
        output=OutputRequest(),
    )

    resolved = resolve_execution_v2(config, request)

    assert resolved.plan.ok
    assert resolved.semantic is not None
    assert resolved.semantic.plan.document_metadata["voice_bindings"] == {
        "kokoro": {"host": "af_bella"}
    }
    binding = resolved.plan.render.role_bindings[0]
    assert binding.role == "host"
    assert binding.target.voice.value == "af_bella"
    assert binding.origin == "document"
    assert (
        next(
            decision
            for decision in resolved.plan.decisions
            if decision.field == "ssmd.bindings.host"
        ).value
        == "af_bella"
    )
