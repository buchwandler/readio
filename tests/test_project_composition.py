from __future__ import annotations

from project_support import Adapter, request

from readio.config import ReaderSettings, ReadioConfig
from readio.engines.registry import _registry
from readio.project import init_project
from readio.stages.composition import compose_project
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
    monkeypatch.setattr("readio.engines.registry.get_engine", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("TTS touched")))
    first = compose_project(project, target_lufs=-18)
    second = compose_project(project, target_lufs=-20)
    assert first["composition_id"] != second["composition_id"]
    assert project.paths["composition_master"].is_file()
    from audiocompose import AudioJob

    job = AudioJob.load(project.paths["composition_audiojob"])
    assert job.items
    assert all("composition" in str(item.source.path) for item in job.items)
