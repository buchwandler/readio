from __future__ import annotations

from project_support import Adapter, request

from readio.config import ReaderSettings, ReadioConfig
from readio.engines.registry import _registry
from readio.project import init_project
from readio.stages.composition import compose_project
from readio.stages.export import export_project
from readio.stages.planning import plan_project
from readio.stages.synthesis import synthesize_project


def test_export_identity_is_after_composition(tmp_path, monkeypatch):
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "book.txt"
    source.write_text("Alpha.\n\nBeta.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    plan_project(project, cfg)
    synthesize_project(project, cfg, request=request(project))
    compose_project(project)
    first = export_project(project, audio_format="wav")
    second = export_project(project, audio_format="ogg")
    assert first["export_id"] != second["export_id"]
    assert first["path"].is_file() and second["path"].is_file()
