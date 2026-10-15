from __future__ import annotations

import json
from contextlib import contextmanager

import numpy as np
from utterplan import LinguisticsConfig, PlannerConfig, compile_document

from voicerender import EngineSelection, RenderedSpeech, VoiceBinding, VoiceProfile

from readio.integrations import voicerender as integration


def test_resolve_profile_uses_voice_render_public_resolution(monkeypatch) -> None:
    calls = []
    selection = EngineSelection(
        engine="fixture",
        target_id="fixture-model",
        language="en-us",
        voice="fixture-voice",
        options={"speed": 1.1},
    )

    def resolve(request):
        calls.append(request)
        return selection, ()

    monkeypatch.setattr(integration, "resolve_voice", resolve)
    resolved = integration.resolve_profile(
        {
            "narrator": {
                "engine": "fixture",
                "target_id": "fixture-model",
                "language": "en-us",
                "voice": "fixture-voice",
                "options": {"speed": 1.1},
            }
        },
        name="test profile",
        default_role="narrator",
        language="en-us",
    )

    assert len(calls) == 1
    assert calls[0].engine == "fixture"
    assert calls[0].target_id == "fixture-model"
    assert isinstance(resolved.profile, VoiceProfile)
    assert isinstance(resolved.profile.roles["narrator"], VoiceBinding)
    assert resolved.profile.roles["narrator"].target_id == "fixture-model"
    assert resolved.selections["narrator"] == selection


def test_render_plan_uses_public_resolution_and_writes_stage_manifest(
    monkeypatch, tmp_path
) -> None:
    plan = compile_document(
        "Hello from the generic VoiceRender adapter.",
        input_format="plain",
        config=PlannerConfig(
            language="en-us",
            document_format="plain",
            linguistics=LinguisticsConfig(use_spacy=False),
        ),
    ).plan
    profile = VoiceProfile(
        name="test",
        roles={"narrator": VoiceBinding("fixture", "fixture-model", voice="fixture-voice")},
    )
    selection = EngineSelection(
        engine="fixture",
        target_id="fixture-model",
        language="en-us",
        voice="fixture-voice",
    )
    requests = []

    class Session:
        def synthesize(self, request):
            requests.append(request)
            return RenderedSpeech(
                id=request.id,
                audio=np.ones(240, dtype=np.float32),
                sample_rate=24_000,
            )

    monkeypatch.setattr(integration, "resolve_voice", lambda _request: (selection, ()))

    @contextmanager
    def open_session(_selection):
        yield Session()

    monkeypatch.setattr("voicerender.open_session", open_session)
    events = []
    output_dir = tmp_path / "render"
    bundle = integration.render_plan(
        plan,
        profile=profile,
        output_dir=output_dir,
        cache_dir=tmp_path / "cache",
        locators=("f000000.s000",),
        on_event=events.append,
    )

    manifest = json.loads(bundle.result.manifest_path.read_text(encoding="utf-8"))
    assert len(requests) == 1
    assert requests[0].text == "Hello from the generic VoiceRender adapter."
    assert bundle.result.rendered_count == 1
    assert manifest["schema"] == "readio.voicerender-compat.v1"
    assert manifest["segments"][0]["source"]["locator"] == "f000000.s000"
    assert {event.kind for event in events} >= {
        "preflight_started",
        "segment_started",
        "segment_rendered",
        "preflight_finished",
    }


def test_all_engine_catalog_skips_unavailable_but_explicit_request_preserves_error(monkeypatch):
    target = integration.SynthesisTarget(
        engine="available",
        id="fixture",
        display_name="Fixture",
    )
    monkeypatch.setattr(
        integration,
        "voice_render_list_engines",
        lambda: ({"id": "missing"}, {"id": "available"}),
    )

    def discover(engine, **_kwargs):
        if engine == "missing":
            raise integration.EngineUnavailableError("engine package is missing")
        return (target,)

    monkeypatch.setattr(integration, "voice_render_discover_targets", discover)
    result = integration.discover_catalog_targets()
    assert result.targets == (target,)
    try:
        integration.discover_catalog_targets("missing")
    except integration.EngineUnavailableError as error:
        assert str(error) == "engine package is missing"
    else:
        raise AssertionError("explicit engine discovery must preserve VoiceRender errors")
