from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from readio.api import ExportOptions, ProjectBuildRequest, ProjectSettings
from readio.project import hash_file, init_project
from readio.project_settings import with_project_settings
from readio.stages import pipeline
from readio.stages.export import export_project, output_state_for


def _audioexport_or_skip():
    try:
        audioexport = importlib.import_module("audioexport")
    except ModuleNotFoundError:
        pytest.skip("AudioExport optional dependency is not installed")
    if not all(
        hasattr(audioexport, name)
        for name in ("load_profile", "resolve_output", "preflight_profile", "encode")
    ):
        pytest.skip("AudioExport 0.1.1 public profile API is not installed")
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("profile export integration requires FFmpeg and FFprobe")
    if not audioexport.doctor().get("formats", {}).get("mp3", {}).get("available"):
        pytest.skip("AudioExport MP3 encoder is unavailable")
    return audioexport


def _project_with_master(tmp_path: Path):
    source = tmp_path / "book.txt"
    source.write_text("fixture", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    master = project.paths["composition_master"]
    master.parent.mkdir(parents=True, exist_ok=True)
    sf.write(master, np.zeros(16_000, dtype=np.float32), 16_000)
    return project, master


def _write_profile(path: Path, *, title: str = "Original") -> None:
    path.write_text(
        'schema = "audioexport.profile.v1"\n'
        f'[metadata]\ntitle = "{title}"\n'
        '[[outputs]]\nformat = "mp3"\nfilename = "profile-name.mp3"\nbitrate = "96k"\n',
        encoding="utf-8",
    )


def _set_composition_state(project, master: Path) -> bytes:
    project.paths["synthesis_profile"].parent.mkdir(parents=True, exist_ok=True)
    project.paths["synthesis_profile"].write_text(
        json.dumps({"profile_id": "synthesis-id"}), encoding="utf-8"
    )
    state = {
        "composition_id": "composition-id",
        "synthesis_profile_id": "synthesis-id",
        "master_sha256": hash_file(master),
        "identity_payload": {},
    }
    state_path = project.paths["composition_state"]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return state_path.read_bytes()


def _configure_profile(project, profile: Path) -> None:
    project.manifest = with_project_settings(
        project.manifest,
        ProjectSettings(export=ExportOptions(profile=profile)),
        project.state_root,
    )


def _mock_current_composition(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pipeline,
        "semantic_status",
        lambda _project: [
            {"stage": "source", "state": "current"},
            {"stage": "workspace", "state": "current"},
            {"stage": "document", "state": "current"},
            {"stage": "plan", "state": "current"},
        ],
    )
    monkeypatch.setattr(
        pipeline, "_synthesis_status", lambda _project: {"stage": "synthesis", "state": "current"}
    )
    monkeypatch.setattr(
        pipeline,
        "build_audio_job",
        lambda *_args, **_kwargs: (None, {"composition_id": "composition-id"}),
    )


def test_saved_profile_status_tracks_identity_and_custom_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _audioexport_or_skip()
    project, master = _project_with_master(tmp_path)
    profile = tmp_path / "export.toml"
    _write_profile(profile)
    exported = export_project(project, profile=profile, format_explicit=False)
    composition_bytes = _set_composition_state(project, master)
    _configure_profile(project, profile)
    _mock_current_composition(monkeypatch)

    current = {row["stage"]: row for row in pipeline.project_status(project)["stages"]}
    assert current["output"]["state"] == "current"
    assert current["output"]["format"] == "mp3"
    assert exported["path"] == project.state_root / "output" / "profile-name.mp3"

    master_before = master.read_bytes()
    _write_profile(profile, title="Changed title")
    stale = {row["stage"]: row for row in pipeline.project_status(project)["stages"]}
    assert stale["output"]["state"] == "stale"
    assert stale["output"]["reason"] == "output.stale.project_settings_changed"
    assert master.read_bytes() == master_before
    assert project.paths["composition_state"].read_bytes() == composition_bytes


def test_build_reexports_saved_profile_without_rebuilding_upstream_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _audioexport_or_skip()
    project, master = _project_with_master(tmp_path)
    profile = tmp_path / "export.toml"
    _write_profile(profile)
    initial = export_project(project, profile=profile, format_explicit=False)
    composition_bytes = _set_composition_state(project, master)
    synthesis_profile_bytes = project.paths["synthesis_profile"].read_bytes()
    _configure_profile(project, profile)

    master_before = master.read_bytes()
    _write_profile(profile, title="Changed title")
    monkeypatch.setattr(
        pipeline,
        "project_status",
        lambda _project: {"stages": [{"stage": "plan", "state": "current"}]},
    )
    monkeypatch.setattr(pipeline, "_project_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        pipeline,
        "synthesize_project",
        lambda *_args, **_kwargs: {
            "rendered": 0,
            "reused": 1,
            "profile": SimpleNamespace(profile_id="synthesis-id"),
        },
    )
    monkeypatch.setattr(
        pipeline,
        "build_audio_job",
        lambda *_args, **_kwargs: (
            None,
            {
                "composition_id": "composition-id",
                "identity_payload": {"loudness": {"profile": "spoken-word"}},
            },
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "compose_project",
        lambda *_args, **_kwargs: pytest.fail("export-only profile edits must not recompose"),
    )

    result = pipeline.build_project(project, object(), ProjectBuildRequest())
    actions = {operation["stage"]: operation["action"] for operation in result["operations"]}
    assert actions == {
        "plan": "skipped",
        "synthesis": "skipped",
        "composition": "skipped",
        "export": "rebuilt",
    }
    assert result["output_path"] == initial["path"]
    assert result["output_path"].is_file()
    updated_state = output_state_for(project, result["output_path"])
    assert updated_state is not None
    assert updated_state["export_id"] != initial["export_id"]
    assert master.read_bytes() == master_before
    assert project.paths["composition_state"].read_bytes() == composition_bytes
    assert project.paths["synthesis_profile"].read_bytes() == synthesis_profile_bytes


def test_clearing_saved_profile_keeps_outputs_and_legacy_wav_available(tmp_path: Path) -> None:
    _audioexport_or_skip()
    project, _master = _project_with_master(tmp_path)
    legacy_before = export_project(project, audio_format="wav")
    legacy_state = output_state_for(project, legacy_before["path"])
    assert legacy_state is not None and legacy_state["schema_version"] == 2
    profile = tmp_path / "export.toml"
    _write_profile(profile)
    profiled = export_project(project, profile=profile, format_explicit=False)
    profile_bytes = profiled["path"].read_bytes()
    profile_state = output_state_for(project, profiled["path"])
    assert profile_state is not None
    sidecar = Path(profile_state["audioexport_sidecar"])
    sidecar_bytes = sidecar.read_bytes()

    project.manifest = with_project_settings(
        project.manifest, ProjectSettings(), project.state_root
    )
    legacy = export_project(project, audio_format="wav")
    index = json.loads((project.state_root / "output" / "state.json").read_text())

    assert legacy["format"] == "wav"
    assert legacy["path"].is_file()
    assert legacy["export_id"] == legacy_before["export_id"]
    assert output_state_for(project, legacy["path"]) == legacy_state
    assert set(index["outputs"]) == {"output/book.wav", "output/profile-name.mp3"}
    assert profiled["path"].read_bytes() == profile_bytes
    assert sidecar.read_bytes() == sidecar_bytes
    assert output_state_for(project, profiled["path"]) == profile_state
