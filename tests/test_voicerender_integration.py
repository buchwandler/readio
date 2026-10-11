from __future__ import annotations

from voicerender import EngineSelection, RenderResult, VoiceBinding, VoiceProfile

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


def test_render_plan_delegates_source_selection_manifest_and_progress(monkeypatch, tmp_path):
    plan = tmp_path / "plan.utterplan.toml"
    source_plan = object()
    profile = VoiceProfile(name="test", roles={"narrator": VoiceBinding("fixture", "model")})
    selection = object()
    manifest_path = tmp_path / "render-manifest.json"
    result = RenderResult(
        manifest_path=manifest_path,
        rendered_count=1,
        reused_count=0,
        failed_count=0,
        selected_count=1,
    )
    manifest = {"manifest_id": "sha256:identity"}
    calls = {}

    def select_segments(received_plan, *, select):
        calls["selection"] = (received_plan, select)
        return selection

    def render_utterplan(received_plan, **kwargs):
        calls["render"] = (received_plan, kwargs)
        return result

    def read_manifest(path):
        calls["manifest"] = path
        return manifest

    monkeypatch.setattr(integration, "select_segments", select_segments)
    monkeypatch.setattr(integration, "load_source_plan", lambda received: source_plan)
    monkeypatch.setattr(integration, "render_utterplan", render_utterplan)
    monkeypatch.setattr(integration, "read_render_manifest", read_manifest)
    progress = lambda event: None
    bundle = integration.render_plan(
        plan,
        profile=profile,
        output_dir=tmp_path / "bundle",
        cache_dir=tmp_path / "cache",
        locators=("f000000.s000",),
        on_event=progress,
    )

    assert calls["selection"] == (source_plan, ("f000000.s000",))
    received_plan, kwargs = calls["render"]
    assert received_plan is plan
    assert kwargs["profile"] is profile
    assert kwargs["selection"] is selection
    assert kwargs["on_event"] is progress
    assert calls["manifest"] == manifest_path
    assert bundle.result is result
    assert bundle.manifest is manifest



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
