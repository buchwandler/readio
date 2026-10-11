from __future__ import annotations

from types import SimpleNamespace

from voicerender import EngineSelection, RenderResult

from readio.config import ReaderSettings, ReadioConfig
from readio.integrations.voicerender import RenderBundle, ResolvedVoiceProfile
from readio.plan import InputRequest, OutputRequest, PlanRequest, SynthesisRequest
from readio.planning.compiler import FlowPlan
from readio.project import hash_file, init_project, read_json
from readio.stages import synthesis
from readio.stages.planning import plan_project


def test_project_synthesis_persists_voice_render_manifest_references(tmp_path, monkeypatch):
    source = tmp_path / "story.ssmd"
    source.write_text(
        '---\nssmd_version: "0.9"\n---\nFirst sentence. Second sentence.',
        encoding="utf-8",
    )
    project = init_project(source, tmp_path / "story.readio")
    config = ReadioConfig(reader=ReaderSettings(engine="fixture", voice="fixture-voice", spacy="off"))
    plan_project(project, config)
    plan_scope = project.load_plan_index().scopes[0]
    plan_path = project.state_root / "plan" / plan_scope.path
    plan = FlowPlan.load(plan_path)
    request = PlanRequest(
        operation="render",
        input=InputRequest(document=project.document()),
        synthesis=SynthesisRequest(engine="fixture", voice="fixture-voice"),
        output=OutputRequest(mode="file", requested_format="wav", force=True),
    )
    selection = EngineSelection(
        engine="fixture",
        target_id="fixture-model",
        language="en-us",
        voice="fixture-voice",
    )
    monkeypatch.setattr(
        synthesis,
        "resolve_project_synthesis",
        lambda _project, _cfg, current: (
            current,
            SimpleNamespace(selection=selection),
            None,
            synthesis.SynthesisProfile("sha256:" + "1" * 64, {}, schema_version=6),
        ),
    )
    monkeypatch.setattr(
        synthesis,
        "resolve_voicerender_profile",
        lambda *_args, **_kwargs: ResolvedVoiceProfile(
            profile=object(), selections={"narrator": selection}
        ),
    )
    render_calls = []

    def render_plan(
        source_plan,
        *,
        profile,
        output_dir,
        cache_dir,
        locators,
        on_event,
    ):
        source_plan = FlowPlan.load(source_plan)
        assert source_plan.plan_id == plan.plan_id
        assert profile is not None
        assert cache_dir == project.state_root / "synthesis" / "cache"
        render_calls.append((output_dir, tuple(locators), on_event))
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = output_dir / "render-manifest.json"
        manifest_path.write_text("{}", encoding="utf-8")
        entries = []
        for locator in locators:
            flow_index, segment_index = map(int, locator[1:].split(".s"))
            flow = source_plan.flow[flow_index]
            segment = flow.segments[segment_index]
            entries.append(
                {
                    "source": {
                        "locator": locator,
                        "semantic_segment_hash": "sha256:" + "2" * 64,
                    },
                    "status": "rendered",
                    "acoustic_cache_key": "sha256:" + "3" * 64,
                    "synthesis_semantics_sha256": "sha256:" + "4" * 64,
                    "audio": {
                        "path": f"segments/{locator.replace('.', '-')}.wav",
                        "sha256": "5" * 64,
                        "sample_rate": 22050,
                        "channels": 1,
                        "frames": 100,
                        "encoding": "PCM_16",
                    },
                    "timing": {"spans": []},
                    "text": segment.text,
                }
            )
        manifest = {
            "manifest_id": "sha256:" + "6" * 64,
            "source": {
                "plan_id": source_plan.plan_id,
                "plan_file_sha256": hash_file(plan_path),
            },
            "profile": {"effective_profile_sha256": "sha256:" + "7" * 64},
            "segments": entries,
        }
        return RenderBundle(
            RenderResult(
                manifest_path=manifest_path,
                rendered_count=len(entries),
                reused_count=0,
                failed_count=0,
                selected_count=len(entries),
            ),
            manifest,
        )

    monkeypatch.setattr(synthesis, "voicerender_render_plan", render_plan)
    events = []
    result = synthesis.synthesize_project(project, config, request=request, on_event=events.append)

    assert result["rendered"] == len(plan.flow)
    assert len(render_calls) == 1
    assert render_calls[0][1] == tuple(
        f"f{flow_index:06d}.s{segment_index:03d}"
        for flow_index, flow in enumerate(plan.flow)
        for segment_index, _segment in enumerate(flow.segments)
    )
    trace = read_json(project.paths["synthesis_trace"])
    assert trace["schema_version"] == 4
    assert trace["manifests"][0]["manifest_id"] == "sha256:" + "6" * 64
    assert len(trace["segments"]) == len(plan.flow)
    assert all("manifest_id" in item and "source_locator" in item for item in trace["segments"])
    assert all(item["path"].startswith("synthesis/renders/") for item in trace["segments"])
    assert not list((project.state_root / "synthesis" / "segments").glob("*.wav"))
    assert events[-1].kind == "complete"
    assert all(artifact.manifest_path is not None for artifact in result["artifacts"])
