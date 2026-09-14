from __future__ import annotations

import pytest

from readio.backends import registry
from readio.config import ReadioConfig, SSMDSettings, VoiceProviderSettings, with_overrides
from readio.document import InputDocument
from readio.plan import InputRequest, PlanRequest, SynthesisRequest, resolve_plan
from readio.reader import pipeline_config_for_document
from readio.synthesis import ResolvedSynthesis


class FixtureBackend:
    id = "fixture"
    ssmd_provider = "fixture"
    supported_options = frozenset()

    def pipeline_config_for_document(self, document, cfg, **kwargs):
        return {"backend": self.id, "document": document.text}


def test_generic_reader_configuration_dispatches_to_selected_backend(monkeypatch) -> None:
    backend = FixtureBackend()
    monkeypatch.setitem(registry._BACKENDS, backend.id, backend)
    cfg = with_overrides(ReadioConfig(), engine=backend.id)
    synthesis = ResolvedSynthesis(
        language="en",
        model=None,
        source=None,
        quality=None,
        voice=None,
        lexicons=None,
        allow_experimental=False,
        speed=1.0,
        pause_mode="auto",
        unit="paragraph",
        engine=backend.id,
    )

    result = pipeline_config_for_document(
        InputDocument(text="hello", source_path=None, format="text"),
        cfg,
        synthesis=synthesis,
    )

    assert result == {"backend": "fixture", "document": "hello"}


def test_unknown_backend_remains_a_stable_lookup_error() -> None:
    with pytest.raises(ValueError, match="Unknown synthesis backend 'fixture'"):
        registry.get_backend("fixture")


def test_ssmd_provider_must_match_selected_backend() -> None:
    cfg = ReadioConfig(
        ssmd=SSMDSettings(voice_provider="fixture"),
        voices={"fixture": VoiceProviderSettings(ids=("fixture-voice",), roles={})},
    )
    request = PlanRequest(
        operation="render",
        input=InputRequest(
            document=InputDocument(
                text='<div voice="narrator">hello</div>',
                source_path=None,
                format="ssmd",
            )
        ),
        synthesis=SynthesisRequest(engine="pykokoro"),
    )

    plan = resolve_plan(cfg, request)

    assert not plan.ok
    assert any(
        diagnostic.code == "ssmd.provider_backend_mismatch" for diagnostic in plan.diagnostics
    )
