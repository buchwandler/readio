from __future__ import annotations

from project_support import Adapter, request

from readio.config import ReaderSettings, ReadioConfig
from readio.engines.registry import _registry
from readio.project import hash_file, init_project, read_json
from readio.stages.composition import build_audio_job, compose_project
from readio.stages.pipeline import preview_project
from readio.stages.planning import plan_project
from readio.stages.synthesis import synthesize_project


def test_composition_uses_persisted_audio_and_loudness_only_rebuild(tmp_path, monkeypatch):
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "book.txt"
    source.write_text("Alpha.\n\nBeta.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    plan_project(project, cfg)
    synthesize_project(project, cfg, request=request(project))
    _, speech_identity = build_audio_job(project)
    _, override_identity = build_audio_job(
        project,
        mastering="peak-safe",
        target_lufs=-16.0,
        true_peak_ceiling_dbtp=-1.0,
    )
    assert speech_identity["composition_id"] != override_identity["composition_id"]
    monkeypatch.setattr(
        "readio.engines.registry.get_engine",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("TTS touched")),
    )
    first = compose_project(project, target_lufs=-18)
    second = compose_project(project, target_lufs=-20)
    assert first["composition_id"] != second["composition_id"]
    assert project.paths["composition_master"].is_file()
    from audiocompose import AudioJob

    job = AudioJob.load(project.paths["composition_audiojob"])
    assert job.items
    assert all("composition" in str(item.source.path) for item in job.items)


def test_compose_project_forwards_progress_and_outer_phases(tmp_path, monkeypatch):
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "book.txt"
    source.write_text("Alpha.\n\nBeta.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    plan_project(project, cfg)
    synthesize_project(project, cfg, request=request(project))
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


def test_progress_does_not_change_composition_identity_or_audio(tmp_path, monkeypatch):
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "book.txt"
    source.write_text("Alpha.\n\nBeta.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    plan_project(project, cfg)
    synthesize_project(project, cfg, request=request(project))
    first = compose_project(project, target_lufs=-18)
    master_bytes = project.paths["composition_master"].read_bytes()
    timeline_bytes = project.paths["composition_timeline"].read_bytes()
    state = read_json(project.paths["composition_state"])
    events = []
    second = compose_project(project, target_lufs=-18, on_progress=events.append)
    assert first["composition_id"] == second["composition_id"]
    assert project.paths["composition_master"].read_bytes() == master_bytes
    assert project.paths["composition_timeline"].read_bytes() == timeline_bytes
    second_state = read_json(project.paths["composition_state"])
    timing_fields = {"analysis_seconds", "gain_seconds", "post_gain_metrics_seconds"}
    assert second_state["identity_payload"] == state["identity_payload"]
    assert not timing_fields.intersection(second_state["identity_payload"].get("loudness", {}))
    assert {key: value for key, value in second_state.items() if key != "loudness"} == {
        key: value for key, value in state.items() if key != "loudness"
    }
    assert {
        key: value for key, value in second_state["loudness"].items() if key not in timing_fields
    } == {key: value for key, value in state["loudness"].items() if key not in timing_fields}
    assert events[-1].kind == "compose_completed"
    assert hash_file(project.paths["composition_master"]) == state["master_sha256"]


def test_preview_forwards_composition_progress(tmp_path, monkeypatch):
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "book.txt"
    source.write_text("Alpha.\n\nBeta.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    plan_project(project, cfg)
    synthesize_project(project, cfg, request=request(project))
    events = []
    preview_project(
        project,
        cfg,
        request=request(project),
        selector="all",
        on_composition_progress=events.append,
    )
    assert events[0].kind == "compose_started"
    assert events[-1].kind == "compose_completed"


def test_project_composition_honors_requested_sample_rate(tmp_path, monkeypatch):
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "book.txt"
    source.write_text("Alpha.\n\nBeta.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    plan_project(project, cfg)
    synthesize_project(project, cfg, request=request(project))

    result = compose_project(project, output_sample_rate=16000)

    assert result["sample_rate"] == 16000
    assert read_json(project.paths["composition_state"])["identity_payload"]["sample_rate"] == 16000
