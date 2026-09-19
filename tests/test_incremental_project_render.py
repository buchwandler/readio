from __future__ import annotations

from project_support import Adapter

from readio.config import ReaderSettings, ReadioConfig
from readio.document import document_from_text
from readio.engines.registry import _registry
from readio.project import init_project
from readio.stages.pipeline import project_status, render_project
from readio.stages.planning import plan_project, plan_project_scope


def test_render_rebuilds_only_stale_stages(tmp_path, monkeypatch):
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "book.txt"
    source.write_text("Alpha.\n\nBeta.\n\nGamma.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    plan_project(project, cfg)
    first = render_project(project, cfg, audio_format="wav")
    assert [row["action"] for row in first["operations"]] == [
        "skipped",
        "rebuilt",
        "rebuilt",
        "rebuilt",
    ]
    second = render_project(project, cfg, audio_format="wav")
    assert [row["action"] for row in second["operations"]] == [
        "skipped",
        "skipped",
        "skipped",
        "skipped",
    ]
    assert adapter.open_calls == 1
    project_source = project.root / "source" / "book.txt"
    project_source.write_text("Alpha.\n\nChanged.\n\nGamma.", encoding="utf-8")
    edited = render_project(project, cfg, audio_format="wav")
    assert edited["operations"][0]["action"] == "rebuilt"
    assert edited["operations"][1]["rendered"] == 1
    assert adapter.open_calls == 2
    loudness = render_project(project, cfg, audio_format="wav", target_lufs=-18)
    assert loudness["operations"][1]["rendered"] == 0
    assert loudness["operations"][2]["action"] == "rebuilt"
    encoded = render_project(project, cfg, audio_format="ogg", target_lufs=-18)
    assert encoded["operations"][1]["rendered"] == 0
    assert encoded["operations"][2]["action"] == "skipped"
    assert encoded["operations"][3]["action"] == "rebuilt"
    assert project_status(project)["stages"][-1]["state"] == "current"


def test_chapter_scope_replacement_preserves_other_scope(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("Base.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    cfg = ReadioConfig()
    plan_project_scope(project, cfg, "ch-0001", document_from_text("Chapter one."))
    plan_project_scope(project, cfg, "ch-0002", document_from_text("Chapter two."))
    before = project.load_plan_index()
    first_id = before.scopes[0].plan_id
    plan_project_scope(project, cfg, "ch-0002", document_from_text("Changed chapter two."))
    after = project.load_plan_index()
    assert len(after.scopes) == 2
    assert after.scopes[0].id == "ch-0001"
    assert after.scopes[0].plan_id == first_id
    assert after.scopes[1].id == "ch-0002"
    assert after.scopes[1].plan_id != before.scopes[1].plan_id
