from __future__ import annotations

import importlib
import json
import shutil
import struct
import zlib
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from readio.integrations.audioexport import (
    AudioExportIntegrationError,
    resolve_profile_export,
)
from readio.project import init_project
from readio.stages.export import export_project, is_export_current, output_state_for


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
    return audioexport


def _project_with_master(tmp_path: Path):
    source = tmp_path / "book.txt"
    source.write_text("fixture", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    master = project.paths["composition_master"]
    master.parent.mkdir(parents=True, exist_ok=True)
    sf.write(master, np.zeros(16_000, dtype=np.float32), 16_000)
    return project, master


def _write_profile(path: Path, *, title: str = "Original", outputs: str | None = None):
    output_rows = outputs or (
        '[[outputs]]\nformat = "mp3"\nfilename = "profile-name.mp3"\nbitrate = "96k"\n'
    )
    path.write_text(
        f'schema = "audioexport.profile.v1"\n[metadata]\ntitle = "{title}"\n{output_rows}',
        encoding="utf-8",
    )


def _write_png(path: Path, color: tuple[int, int, int]) -> None:
    def chunk(name: bytes, value: bytes) -> bytes:
        return (
            struct.pack(">I", len(value))
            + name
            + value
            + struct.pack(">I", zlib.crc32(name + value) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    image = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
    image += chunk(b"IDAT", zlib.compress(b"\x00" + bytes(color))) + chunk(b"IEND", b"")
    path.write_bytes(image)


def _write_resource_profile(path: Path, *, title: str = "Resource title", bitrate: str = "96k"):
    path.write_text(
        'schema = "audioexport.profile.v1"\n'
        'cover = "cover.png"\n'
        'timeline = "timeline.json"\n'
        f'[metadata]\ntitle = "{title}"\n'
        '[[outputs]]\nformat = "m4a"\nfilename = "resource.m4a"\n'
        f'bitrate = "{bitrate}"\nuse_cover = true\nuse_chapters = true\n',
        encoding="utf-8",
    )


def test_profile_export_uses_audioexport_and_readio_ownership(tmp_path: Path):
    audioexport = _audioexport_or_skip()
    report = audioexport.doctor()
    if not report.get("formats", {}).get("mp3", {}).get("available"):
        pytest.skip("AudioExport MP3 encoder is unavailable")

    project, _master = _project_with_master(tmp_path)
    profile = tmp_path / "export.toml"
    _write_profile(profile)

    result = export_project(project, profile=profile, format_explicit=False)
    target = project.state_root / "output" / "profile-name.mp3"
    state = output_state_for(project, target)
    assert state is not None
    assert result["path"] == target
    assert result["format"] == "mp3"
    assert target.is_file()
    assert Path(state["audioexport_sidecar"]).is_file()
    assert state["backend"] == "audioexport"
    assert state["audioexport_version"] == audioexport.__version__
    assert is_export_current(
        project, target, audio_format="wav", profile=profile, format_explicit=False
    )
    explicit_output = tmp_path / "explicit.mp3"
    explicit = export_project(
        project,
        profile=profile,
        format_explicit=False,
        output=explicit_output,
        bitrate="160k",
    )
    assert explicit["path"] == explicit_output
    overridden_state = output_state_for(project, explicit_output)
    assert overridden_state is not None
    assert overridden_state["options"]["bitrate"] == "160k"

    first_id = result["export_id"]
    profile.write_text(
        profile.read_text(encoding="utf-8") + "\n# comment-only change\n", encoding="utf-8"
    )
    assert is_export_current(
        project, target, audio_format="wav", profile=profile, format_explicit=False
    )

    _write_profile(profile, title="Changed title")
    assert not is_export_current(
        project, target, audio_format="wav", profile=profile, format_explicit=False
    )
    changed = export_project(project, profile=profile, format_explicit=False)
    assert changed["export_id"] != first_id

    target.write_bytes(b"externally modified")
    with pytest.raises(FileExistsError, match="not an unchanged Readio export"):
        export_project(project, profile=profile, format_explicit=False)
    assert target.read_bytes() == b"externally modified"
    forced = export_project(project, profile=profile, format_explicit=False, force=True)
    assert forced["path"] == target
    assert target.read_bytes() != b"externally modified"


def test_audioexport_failure_preserves_last_good_readio_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    audioexport = _audioexport_or_skip()
    report = audioexport.doctor()
    if not report.get("formats", {}).get("mp3", {}).get("available"):
        pytest.skip("AudioExport MP3 encoder is unavailable")

    project, _master = _project_with_master(tmp_path)
    profile = tmp_path / "export.toml"
    _write_profile(profile)
    first = export_project(project, profile=profile, format_explicit=False)
    target = first["path"]
    before_bytes = target.read_bytes()
    before_state = output_state_for(project, target)
    assert before_state is not None

    def fail_encode(*_args, **_kwargs):
        raise audioexport.EncodingError(
            "injected encoder failure", code="audioexport.encoding_failed"
        )

    monkeypatch.setattr(audioexport, "encode", fail_encode)
    with pytest.raises(AudioExportIntegrationError) as failure:
        export_project(project, profile=profile, format_explicit=False)
    assert failure.value.code == "audioexport.encoding_failed"
    assert target.read_bytes() == before_bytes
    assert output_state_for(project, target) == before_state


@pytest.mark.parametrize("audio_format", ["flac", "ogg", "opus"])
def test_profile_export_supports_remaining_non_wav_formats(tmp_path: Path, audio_format: str):
    audioexport = _audioexport_or_skip()
    report = audioexport.doctor()
    if not report.get("formats", {}).get(audio_format, {}).get("available"):
        pytest.skip(f"AudioExport {audio_format.upper()} encoder is unavailable")

    project, _master = _project_with_master(tmp_path)
    profile = tmp_path / f"{audio_format}.toml"
    _write_profile(profile, outputs=f'[[outputs]]\nformat = "{audio_format}"\n')
    result = export_project(project, profile=profile, format_explicit=False)
    target = project.state_root / "output" / f"book.{audio_format}"
    state = output_state_for(project, target)
    assert result["format"] == audio_format
    assert result["path"] == target
    assert target.is_file()
    assert state is not None and state["backend"] == "audioexport"


def test_profile_selection_and_wav_metadata_are_explicit(tmp_path: Path):
    _audioexport_or_skip()
    project, master = _project_with_master(tmp_path)
    profile = tmp_path / "multi.toml"
    _write_profile(
        profile,
        outputs=('[[outputs]]\nformat = "mp3"\n[[outputs]]\nformat = "flac"\n'),
    )
    with pytest.raises(AudioExportIntegrationError) as missing_format:
        resolve_profile_export(
            profile,
            master=master,
            source_stem=project.manifest.name,
            requested_format="wav",
            format_explicit=False,
            bitrate_override=None,
            preflight=False,
        )
    assert missing_format.value.code == "readio.export.profile_format_required"

    wav_profile = tmp_path / "wav.toml"
    _write_profile(
        wav_profile,
        title="Metadata cannot be silently dropped",
        outputs='[[outputs]]\nformat = "wav"\n',
    )
    with pytest.raises(AudioExportIntegrationError) as wav_metadata:
        resolve_profile_export(
            wav_profile,
            master=master,
            source_stem=project.manifest.name,
            requested_format="wav",
            format_explicit=False,
            bitrate_override=None,
            preflight=False,
        )
    assert wav_metadata.value.code == "readio.export.profile_wav_metadata_unsupported"


def test_resource_profile_identity_tracks_effective_values_only(tmp_path: Path):
    audioexport = _audioexport_or_skip()
    report = audioexport.doctor()
    if not report.get("formats", {}).get("m4a", {}).get("available"):
        pytest.skip("AudioExport M4A encoder is unavailable")

    project, master = _project_with_master(tmp_path)
    profile = tmp_path / "resources.toml"
    cover = tmp_path / "cover.png"
    timeline = tmp_path / "timeline.json"
    _write_png(cover, (255, 0, 0))
    original_timeline = json.dumps(
        {
            "sample_rate": 16_000,
            "chapters": [
                {"title": "First", "start_sample": 0},
                {"title": "Second", "start_sample": 8_000},
            ],
        }
    )
    timeline.write_text(original_timeline, encoding="utf-8")
    _write_resource_profile(profile)

    resolved = resolve_profile_export(
        profile,
        master=master,
        source_stem=project.manifest.name,
        requested_format="m4a",
        format_explicit=True,
        bitrate_override=None,
    )
    assert resolved.cover == cover.resolve()
    assert resolved.timeline == timeline.resolve()
    assert resolved.bitrate == "96k"
    overridden = resolve_profile_export(
        profile,
        master=master,
        source_stem=project.manifest.name,
        requested_format="m4a",
        format_explicit=True,
        bitrate_override="128k",
        preflight=False,
    )
    assert overridden.bitrate == "128k"

    result = export_project(
        project,
        audio_format="m4a",
        profile=profile,
        format_explicit=True,
    )
    target = project.state_root / "output" / "resource.m4a"
    assert result["path"] == target
    assert is_export_current(
        project, target, audio_format="m4a", profile=profile, format_explicit=True
    )

    original_profile = profile.read_text(encoding="utf-8")
    profile.write_text(
        original_profile + '\n# comment only\n[[outputs]]\nformat = "flac"\n',
        encoding="utf-8",
    )
    assert is_export_current(
        project, target, audio_format="m4a", profile=profile, format_explicit=True
    )

    profile.write_text(
        original_profile.replace('bitrate = "96k"', 'bitrate = "128k"'), encoding="utf-8"
    )
    assert not is_export_current(
        project, target, audio_format="m4a", profile=profile, format_explicit=True
    )
    profile.write_text(
        original_profile.replace('title = "Resource title"', 'title = "Changed"'),
        encoding="utf-8",
    )
    assert not is_export_current(
        project, target, audio_format="m4a", profile=profile, format_explicit=True
    )
    profile.write_text(original_profile, encoding="utf-8")

    _write_png(cover, (0, 255, 0))
    assert not is_export_current(
        project, target, audio_format="m4a", profile=profile, format_explicit=True
    )
    _write_png(cover, (255, 0, 0))
    timeline.write_text(original_timeline.replace("Second", "Changed chapter"), encoding="utf-8")
    assert not is_export_current(
        project, target, audio_format="m4a", profile=profile, format_explicit=True
    )
    timeline.write_text(original_timeline, encoding="utf-8")
    assert is_export_current(
        project, target, audio_format="m4a", profile=profile, format_explicit=True
    )
