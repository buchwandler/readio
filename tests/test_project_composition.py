from __future__ import annotations

from audiocompose import AudioClip, AudioFileSource, AudioJob
from voicerender import read_render_manifest
from voicerender.engines.registry import _ADAPTERS
from voicerender_support import FixtureAdapter

from readio.config import ReaderSettings, ReadioConfig
from readio.project import hash_file, init_project, read_json
from readio.stages import composition as composition_stage
from readio.stages.composition import build_audio_job, compose_project, composition_is_current
from readio.stages.planning import plan_project
from readio.stages.synthesis import synthesize_project


def _materialize_for_test(*, plan, render_manifest, pause_policy, output_policy):
    manifest_path = render_manifest
    manifest = read_render_manifest(manifest_path)
    plan_id = manifest["source"]["plan_id"]
    clips = []
    for entry in manifest["segments"]:
        audio = entry["audio"]
        locator = entry["source"]["locator"]
        clips.append(
            AudioClip(
                id=f"speech:{locator}",
                source=AudioFileSource(
                    manifest_path.parent / audio["path"],
                    expected_sha256=audio["sha256"],
                    sample_rate=audio["sample_rate"],
                    channels=audio["channels"],
                    frames=audio["frames"],
                ),
                metadata={
                    "utterplan.plan_id": plan_id,
                    "utterplan.locator": locator,
                },
            )
        )
    assert pause_policy.policy_id
    return AudioJob(items=tuple(clips), output=output_policy)



def _project(tmp_path, monkeypatch, *, text="Alpha.\n\nBeta."):
    adapter = FixtureAdapter()
    monkeypatch.setitem(_ADAPTERS, adapter.id, adapter)
    config = ReadioConfig(
        reader=ReaderSettings(
            engine=adapter.id,
            voice="fixture-voice",
            spacy="off",
        )
    )
    source = tmp_path / "book.txt"
    source.write_text(text, encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    plan_project(project, config)
    synthesize_project(project, config)
    monkeypatch.setattr(composition_stage, "materialize_voicerender_job", _materialize_for_test)
    return project, config


def test_composition_uses_audiojob_and_mastering_rebuilds_without_tts(tmp_path, monkeypatch):
    project, _config = _project(tmp_path, monkeypatch)
    speech_job, _speech_options = build_audio_job(project)
    mastered_job, _mastered_options = build_audio_job(
        project,
        mastering="peak-safe",
        target_lufs=-14.0,
        true_peak_ceiling_dbtp=-1.0,
    )
    assert speech_job.output != mastered_job.output

    monkeypatch.setattr(
        "readio.engines.registry.get_engine",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("TTS touched")),
    )
    first = compose_project(project, target_lufs=-18)
    second = compose_project(project, target_lufs=-20)
    assert first["job_id"] != second["job_id"]
    assert first["composition_id"] == first["job_id"]
    assert project.paths["composition_master"].is_file()

    from audiocompose import AudioJob

    job = AudioJob.load(project.paths["composition_audiojob"])
    assert job.job_id == second["job_id"]
    assert composition_is_current(project, target_lufs=-20)
    assert not composition_is_current(project, target_lufs=-18)
    assert (project.state_root / "composition" / "pause-policy.toml").is_file()
    assert job.items
    assert all("composition" in str(item.source.path) for item in job.items if hasattr(item, "source"))


def test_compose_project_forwards_progress_and_outer_phases(tmp_path, monkeypatch):
    project, _config = _project(tmp_path, monkeypatch)
    events = []
    phases = []
    compose_project(project, on_progress=events.append, on_phase=phases.append)
    assert events[0].kind == "compose_started"
    assert events[-1].kind == "compose_completed"
    assert any(event.kind == "operation_started" for event in events) is False
    assert phases[0] == "Preparing composition"
    assert phases[1].startswith("Composition layout prepared in ")
    assert "Writing composition artifacts" in phases
    for phase in (
        "Writing AudioJob manifest",
        "AudioJob manifest written in ",
        "Writing master WAV",
        "Master WAV written in ",
        "Building and writing composition timeline",
        "Composition timeline written in ",
        "Hashing composition artifacts",
        "Composition artifacts hashed in ",
        "Writing composition state",
        "Composition state written in ",
    ):
        assert any(item.startswith(phase) for item in phases)


def test_progress_does_not_change_job_identity_or_audio(tmp_path, monkeypatch):
    project, _config = _project(tmp_path, monkeypatch)
    first = compose_project(project, target_lufs=-18)
    master_bytes = project.paths["composition_master"].read_bytes()
    timeline_bytes = project.paths["composition_timeline"].read_bytes()
    state = read_json(project.paths["composition_state"])
    events = []
    second = compose_project(project, target_lufs=-18, on_progress=events.append)
    assert first["job_id"] == second["job_id"]
    assert project.paths["composition_master"].read_bytes() == master_bytes
    assert project.paths["composition_timeline"].read_bytes() == timeline_bytes
    second_state = read_json(project.paths["composition_state"])
    assert second_state["identity_payload"] == state["identity_payload"]
    assert not {"analysis_seconds", "gain_seconds", "post_gain_metrics_seconds"}.intersection(
        second_state["identity_payload"].get("output_policy", {})
    )
    timing_fields = {"analysis_seconds", "gain_seconds", "post_gain_metrics_seconds"}
    assert {key: value for key, value in second_state.items() if key != "loudness"} == {
        key: value for key, value in state.items() if key != "loudness"
    }
    assert {
        key: value for key, value in second_state["loudness"].items() if key not in timing_fields
    } == {key: value for key, value in state["loudness"].items() if key not in timing_fields}
    assert events[-1].kind == "compose_completed"
    assert hash_file(project.paths["composition_master"]) == state["master_sha256"]


def test_project_composition_honors_requested_sample_rate(tmp_path, monkeypatch):
    project, _config = _project(tmp_path, monkeypatch)
    result = compose_project(project, output_sample_rate=16000)
    assert result["sample_rate"] == 16000
    assert read_json(project.paths["composition_state"])["output_policy"]["sample_rate"] == 16000



def test_project_status_reuses_audiojob_identity(tmp_path, monkeypatch):
    project, _config = _project(tmp_path, monkeypatch)
    composed = compose_project(project)
    from readio.stages.pipeline import project_status

    stages = {row["stage"]: row for row in project_status(project)["stages"]}
    assert stages["composition"]["state"] == "current", stages["composition"]
    assert stages["composition"]["job_id"] == composed["job_id"]
