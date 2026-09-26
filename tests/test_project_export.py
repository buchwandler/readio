from __future__ import annotations

import json

import pytest
from project_support import Adapter, request

from readio.api.types import ExportOptions, ProjectBuildRequest
from readio.config import ReaderSettings, ReadioConfig
from readio.engines.registry import _registry
from readio.plan import SynthesisRequest
from readio.project import init_project
from readio.stages.composition import compose_project
from readio.stages.export import (
    build_audio_export_identity,
    effective_audio_export_options,
    export_project,
    normalize_bitrate,
    output_state_for,
)
from readio.stages.pipeline import build_project
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


def _composed_project(tmp_path, monkeypatch):
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "book.txt"
    source.write_text("Alpha. Beta.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    plan_project(project, cfg)
    synthesize_project(project, cfg, request=request(project))
    compose_project(project)
    return project, cfg


def test_effective_export_options_normalize_and_change_identity():
    normalized = effective_audio_export_options("m4a", "096K")
    assert normalized == {"bitrate": "96k"}
    assert normalize_bitrate("1M") == "1000k"
    assert build_audio_export_identity(
        master_sha256="master", audio_format="m4a", effective_options=normalized
    ) != build_audio_export_identity(
        master_sha256="master",
        audio_format="m4a",
        effective_options=effective_audio_export_options("m4a", "128k"),
    )


@pytest.mark.parametrize("audio_format", ["wav", "flac", "mp3", "ogg"])
def test_bitrate_is_rejected_when_backend_cannot_honor_it(audio_format):
    with pytest.raises(ValueError, match=f"bitrate is not supported for {audio_format.upper()}"):
        effective_audio_export_options(audio_format, "64k")


def test_opus_default_bitrate_is_explicit():
    assert effective_audio_export_options("opus", None) == {"bitrate": "96k"}


def test_export_tracks_multiple_outputs_and_protects_unowned_files(tmp_path, monkeypatch):
    project, _ = _composed_project(tmp_path, monkeypatch)
    first = export_project(project, audio_format="wav")
    second = export_project(project, audio_format="ogg")
    state = json.loads((project.root / "output" / "state.json").read_text(encoding="utf-8"))
    assert state["format"] == "readio.export-index"
    assert len(state["outputs"]) == 2
    assert first["path"].is_file() and second["path"].is_file()

    tracked = first["path"]
    tracked.write_bytes(b"externally modified")
    with pytest.raises(FileExistsError, match="not an unchanged Readio export"):
        export_project(project, audio_format="wav")
    assert tracked.read_bytes() == b"externally modified"
    export_project(project, audio_format="wav", force=True)
    assert output_state_for(project, tracked) is not None

    untracked = tmp_path / "untracked.wav"
    untracked.write_bytes(b"user file")
    with pytest.raises(FileExistsError):
        export_project(project, audio_format="wav", output=untracked)
    assert untracked.read_bytes() == b"user file"
    export_project(project, audio_format="wav", output=untracked, force=True)


def test_build_freshness_compares_effective_bitrate_identity(tmp_path, monkeypatch):
    project, cfg = _composed_project(tmp_path, monkeypatch)

    class FakeSink:
        def __init__(self, path, bitrate):
            self.path = path
            self.bitrate = bitrate

        def __enter__(self):
            return self

        def write(self, audio, sample_rate):
            self.path.write_bytes(self.bitrate.encode("ascii"))

        def __exit__(self, exc_type, exc_value, traceback):
            return None

    monkeypatch.setattr("readio.stages.export.ensure_audio_format_available", lambda _fmt: None)
    monkeypatch.setattr(
        "readio.stages.export.create_audio_sink",
        lambda path, _fmt, *, bitrate=None: FakeSink(path, bitrate),
    )

    def build(bitrate):
        return build_project(
            project,
            cfg,
            ProjectBuildRequest(
                target="export",
                synthesis=SynthesisRequest(engine="fake", voice="fake-voice"),
                export=ExportOptions(format="m4a", bitrate=bitrate),
            ),
        )

    first = build("096K")
    same_effective = build("96k")
    changed = build("128k")
    assert (
        next(item for item in first["operations"] if item["stage"] == "export")["action"]
        == "rebuilt"
    )
    assert (
        next(item for item in same_effective["operations"] if item["stage"] == "export")["action"]
        == "skipped"
    )
    assert (
        next(item for item in changed["operations"] if item["stage"] == "export")["action"]
        == "rebuilt"
    )
