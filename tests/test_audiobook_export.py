from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf
from audiobook_support import make_epub

from readio.audiobook import init_audiobook_project
from readio.project import atomic_write_json, hash_file, init_project
from readio.stages.audiobook_export import (
    AudiobookExportError,
    audiobook_export_target,
    build_audiobook_export_identity,
    escape_ffmetadata_value,
    export_audiobook_project,
    ffmetadata_text,
    prepare_audiobook_export,
)

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _book_project(tmp_path: Path):
    source = tmp_path / "book.epub"
    make_epub(source)
    return init_audiobook_project(source, tmp_path / "book.ssmdbook")


def test_audiobook_export_target_uses_state_for_defaults_and_relative_paths(tmp_path: Path) -> None:
    project = _book_project(tmp_path)

    assert audiobook_export_target(project, None) == project.state_root / "output" / "book.m4b"
    assert audiobook_export_target(project, Path("exports/custom.m4b")) == (
        project.state_root / "exports" / "custom.m4b"
    )
    external = tmp_path / "outside.m4b"
    assert audiobook_export_target(project, external) == external


def _write_composition(
    project,
    *,
    starts: tuple[int, ...] = (0, 400),
    titles: tuple[str, ...] = ("Chapter One", "Chapter Two"),
    frames: int = 1000,
) -> None:
    master = project.paths["composition_master"]
    master.parent.mkdir(parents=True, exist_ok=True)
    sf.write(master, np.zeros(frames, dtype=np.float32), 24000, subtype="PCM_16")
    timeline = {
        "format": "readio.composition-timeline",
        "schema_version": 2,
        "composition_id": "sha256:composition",
        "sample_rate": 24000,
        "chapters": [
            {
                "scope_id": f"chapter-{index:04d}",
                "source_number": index,
                "title": titles[index - 1] if index <= len(titles) else None,
                "start_sample": start,
            }
            for index, start in enumerate(starts, 1)
        ],
    }
    atomic_write_json(project.paths["composition_timeline"], timeline)
    atomic_write_json(
        project.paths["composition_state"],
        {
            "format": "readio.composition-state",
            "schema_version": 2,
            "composition_id": "sha256:composition",
            "master_sha256": hash_file(master),
            "timeline_sha256": hash_file(project.paths["composition_timeline"]),
            "sample_rate": 24000,
            "frames": frames,
        },
    )


def _refresh_timeline_hash(project) -> None:
    state_path = project.paths["composition_state"]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["timeline_sha256"] = hash_file(project.paths["composition_timeline"])
    atomic_write_json(state_path, state)


def _png_cover(path: Path, payload: bytes = b"first cover") -> Path:
    path.write_bytes(_PNG_SIGNATURE + payload)
    return path


def test_prepare_validates_composition_and_derives_sample_ranges(tmp_path: Path):
    project = _book_project(tmp_path)
    _write_composition(project)

    prepared = prepare_audiobook_export(project)

    assert prepared.sample_rate == 24000
    assert prepared.frames == 1000
    assert [(chapter.start_sample, chapter.end_sample) for chapter in prepared.chapters] == [
        (0, 400),
        (400, 1000),
    ]
    assert prepared.metadata.title == "The Example"
    assert prepared.metadata.author == "A. Writer"
    assert prepared.bitrate == "192k"


def test_title_author_cover_and_bitrate_overrides_are_resolved_and_hashed(tmp_path: Path):
    project = _book_project(tmp_path)
    _write_composition(project)
    cover = _png_cover(tmp_path / "cover.png")

    prepared = prepare_audiobook_export(
        project,
        title="  New Title  ",
        author="New Author",
        cover=cover,
        bitrate="096K",
    )

    assert prepared.metadata.title == "New Title"
    assert prepared.metadata.author == "New Author"
    assert prepared.metadata.cover == cover.resolve()
    assert prepared.metadata.cover_sha256 == hash_file(cover)
    assert prepared.bitrate == "96k"


def test_export_identity_changes_for_each_export_input(tmp_path: Path):
    project = _book_project(tmp_path)
    _write_composition(project)
    baseline = prepare_audiobook_export(project)
    cover_a = _png_cover(tmp_path / "a.png", b"A")
    cover_b = _png_cover(tmp_path / "b.png", b"B")

    variants = (
        prepare_audiobook_export(project, title="Other Title"),
        prepare_audiobook_export(project, author="Other Author"),
        prepare_audiobook_export(project, cover=cover_a),
        prepare_audiobook_export(project, bitrate="128k"),
    )
    assert all(item.export_id != baseline.export_id for item in variants)
    assert prepare_audiobook_export(project, cover=cover_a).export_id != (
        prepare_audiobook_export(project, cover=cover_b).export_id
    )

    timeline_path = project.paths["composition_timeline"]
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    timeline["chapters"][0]["title"] = "Renamed Chapter"
    atomic_write_json(timeline_path, timeline)
    _refresh_timeline_hash(project)
    changed_timeline = prepare_audiobook_export(project)
    assert changed_timeline.timeline_sha256 != baseline.timeline_sha256
    assert changed_timeline.export_id != baseline.export_id


def test_stale_master_or_timeline_requires_recomposition(tmp_path: Path):
    project = _book_project(tmp_path)
    _write_composition(project)
    project.paths["composition_timeline"].write_text("{}", encoding="utf-8")
    with pytest.raises(AudiobookExportError) as stale:
        prepare_audiobook_export(project)
    assert stale.value.code == "audiobook.export.timeline_stale"


@pytest.mark.parametrize(
    ("field", "value"),
    [("composition_id", "sha256:other"), ("sample_rate", 48000)],
)
def test_timeline_must_match_state_identity_and_sample_rate(tmp_path: Path, field: str, value):
    project = _book_project(tmp_path)
    _write_composition(project)
    timeline_path = project.paths["composition_timeline"]
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    timeline[field] = value
    atomic_write_json(timeline_path, timeline)
    _refresh_timeline_hash(project)

    with pytest.raises(AudiobookExportError) as error:
        prepare_audiobook_export(project)
    assert error.value.code == "audiobook.export.timeline_stale"


def test_changed_master_hash_requires_recomposition(tmp_path: Path):
    project = _book_project(tmp_path)
    _write_composition(project)
    sf.write(
        project.paths["composition_master"],
        np.zeros(999, dtype=np.float32),
        24000,
        subtype="PCM_16",
    )
    with pytest.raises(AudiobookExportError) as error:
        prepare_audiobook_export(project)
    assert error.value.code == "audiobook.export.timeline_stale"


def test_missing_composition_and_timeline_have_distinct_errors(tmp_path: Path):
    project = _book_project(tmp_path)
    with pytest.raises(AudiobookExportError) as missing_composition:
        prepare_audiobook_export(project)
    assert missing_composition.value.code == "audiobook.export.composition_missing"

    _write_composition(project)
    project.paths["composition_timeline"].unlink()
    with pytest.raises(AudiobookExportError) as missing_timeline:
        prepare_audiobook_export(project)
    assert missing_timeline.value.code == "audiobook.export.timeline_missing"


def test_non_audiobook_project_is_rejected(tmp_path: Path):
    source = tmp_path / "document.txt"
    source.write_text("Ordinary document", encoding="utf-8")
    project = init_project(source, tmp_path / "document.readio")
    with pytest.raises(AudiobookExportError) as error:
        prepare_audiobook_export(project)
    assert error.value.code == "audiobook.export.not_audiobook"


@pytest.mark.parametrize("starts", [(0, 0), (0, 900, 800), (-1, 400), (0, 1000)])
def test_invalid_chapter_ranges_are_rejected(tmp_path: Path, starts: tuple[int, ...]):
    project = _book_project(tmp_path)
    _write_composition(project, starts=starts)
    with pytest.raises(AudiobookExportError) as error:
        prepare_audiobook_export(project)
    assert error.value.code == "audiobook.export.invalid_chapters"


def test_nonzero_first_chapter_start_is_a_valid_sample_boundary(tmp_path: Path):
    project = _book_project(tmp_path)
    _write_composition(project, starts=(100, 400))
    prepared = prepare_audiobook_export(project)
    assert prepared.chapters[0].start_sample == 100
    assert prepared.chapters[0].end_sample == 400


def test_cover_path_and_type_errors_are_specific(tmp_path: Path):
    project = _book_project(tmp_path)
    _write_composition(project)
    with pytest.raises(AudiobookExportError) as missing:
        prepare_audiobook_export(project, cover=tmp_path / "missing.png")
    assert missing.value.code == "audiobook.export.cover_not_found"

    unsupported = tmp_path / "cover.txt"
    unsupported.write_text("not an image", encoding="utf-8")
    with pytest.raises(AudiobookExportError) as bad_type:
        prepare_audiobook_export(project, cover=unsupported)
    assert bad_type.value.code == "audiobook.export.cover_unsupported"


def test_ffmetadata_values_are_escaped_and_use_sample_timebase(tmp_path: Path):
    project = _book_project(tmp_path)
    _write_composition(project, titles=("Chapter=One;#\\Part\ncontinued", "Last"))
    prepared = prepare_audiobook_export(project, title="Book=Name;#\\\nVol", author="A; Writer")

    assert escape_ffmetadata_value("a=b;#c\\d\nz") == "a\\=b\\;\\#c\\\\d\\nz"
    text = ffmetadata_text(prepared)
    assert text.startswith(";FFMETADATA1\ntitle=Book\\=Name\\;\\#\\\\\\nVol\nartist=A\\; Writer")
    assert "TIMEBASE=1/24000\nSTART=0\nEND=400" in text
    assert "title=Chapter\\=One\\;\\#\\\\Part\\ncontinued" in text


def test_audiobook_identity_includes_all_metadata_and_encoder_options():
    common = {
        "master_sha256": "master",
        "timeline_sha256": "timeline",
        "title": "Book",
        "author": "Author",
        "cover_sha256": "cover",
        "bitrate": "192k",
    }
    original = build_audiobook_export_identity(**common)
    for key, value in (
        ("title", "Changed"),
        ("author", "Changed"),
        ("cover_sha256", "changed-cover"),
        ("bitrate", "96k"),
        ("timeline_sha256", "changed-timeline"),
        ("master_sha256", "changed-master"),
    ):
        changed = dict(common)
        changed[key] = value
        assert build_audiobook_export_identity(**changed) != original


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required for the M4B integration test",
)
@pytest.mark.parametrize("cover_extension", ["jpg", "png"])
def test_export_produces_chapters_metadata_and_attached_cover(
    tmp_path: Path, monkeypatch, cover_extension: str
):
    project = _book_project(tmp_path)
    _write_composition(project, starts=(0, 12000), frames=24000)
    cover = tmp_path / f"cover.{cover_extension}"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=32x32",
            "-frames:v",
            "1",
            "-y",
            str(cover),
        ],
        check=True,
        capture_output=True,
    )
    output = project.state_root / "output" / "finished"

    result = export_audiobook_project(
        project,
        output=output,
        title="Book Title",
        author="Book Author",
        cover=cover,
        bitrate="96k",
    )

    assert result["format"] == "m4b"
    assert result["chapter_count"] == 2
    assert result["path"] == output.with_suffix(".m4b")
    assert result["path"].is_file()
    state_index = json.loads(
        (project.state_root / "output" / "state.json").read_text(encoding="utf-8")
    )
    state = state_index["outputs"]["output/finished.m4b"]
    assert state["format"] == "readio.audiobook-export-state"
    assert state["options"]["bitrate"] == "96k"

    def unexpected_encode(*args, **kwargs):
        raise AssertionError("an identical tracked M4B export should be reused")

    monkeypatch.setattr("readio.stages.audiobook_export.subprocess.run", unexpected_encode)
    reused = export_audiobook_project(
        project,
        output=output,
        title="Book Title",
        author="Book Author",
        cover=cover,
        bitrate="96K",
    )
    assert reused["export_id"] == result["export_id"]
    assert reused["output_sha256"] == result["output_sha256"]


def _audioexport_profile(
    path: Path,
    *,
    cover: Path,
    filename: str = "profile-book.m4b",
    timeline: Path | None = None,
    bitrate: str = "128k",
    use_cover: bool = True,
    title: str = "Profile Book",
    artist: str = "Profile Reader",
) -> Path:
    rows = [
        'schema = "audioexport.profile.v1"',
        f"cover = {json.dumps(str(cover))}",
    ]
    if timeline is not None:
        rows.append(f"timeline = {json.dumps(str(timeline))}")
    rows.extend(
        [
            "",
            "[metadata]",
            f"title = {json.dumps(title)}",
            f"artist = {json.dumps(artist)}",
            'album = "Profile Album"',
            "",
            "[[outputs]]",
            'format = "m4b"',
            f"filename = {json.dumps(filename)}",
            f"bitrate = {json.dumps(bitrate)}",
            f"use_cover = {str(use_cover).lower()}",
            "use_chapters = true",
            "",
        ]
    )
    path.write_text("\n".join(rows), encoding="utf-8")
    return path


def _make_image_cover(path: Path, color: str) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=40x40",
            "-frames:v",
            "1",
            "-y",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required for the M4B profile integration test",
)
@pytest.mark.parametrize("cover_extension", ["jpg", "png"])
def test_audioexport_profile_m4b_uses_readio_chapters_and_reuses_output(
    tmp_path: Path, monkeypatch, cover_extension: str
):
    audioexport = pytest.importorskip("audioexport")
    project = _book_project(tmp_path)
    titles = ("雪=第一章;#\\\\continued", "第二章")
    _write_composition(project, starts=(0, 12000), titles=titles, frames=24000)
    cover = _make_image_cover(tmp_path / f"profile-cover.{cover_extension}", "red")
    profile = _audioexport_profile(tmp_path / "export.toml", cover=cover)
    captured: dict[str, Any] = {}
    encode = audioexport.encode

    def capture_encode(*args, **kwargs):
        captured["chapters"] = kwargs["chapters"]
        return encode(*args, **kwargs)

    monkeypatch.setattr(audioexport, "encode", capture_encode)
    result = export_audiobook_project(project, profile=profile)

    expected = project.state_root / "output" / "profile-book.m4b"
    assert result["path"] == expected
    assert result["chapter_count"] == 2
    assert [(chapter.start_sample, chapter.end_sample) for chapter in captured["chapters"]] == [
        (0, 12000),
        (12000, 24000),
    ]
    assert all(chapter.sample_rate == 24000 for chapter in captured["chapters"])

    probe = json.loads(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_chapters",
                "-show_format",
                "-of",
                "json",
                str(expected),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    audio = [stream for stream in probe["streams"] if stream.get("codec_type") == "audio"]
    tags = {key.lower(): value for key, value in probe["format"]["tags"].items()}
    assert len(audio) == 1 and audio[0]["codec_name"] == "aac"
    assert tags["title"] == "Profile Book"
    assert tags["artist"] == "Profile Reader"
    assert tags["album"] == "Profile Album"
    assert [chapter["tags"]["title"] for chapter in probe["chapters"]] == list(titles)
    assert [
        (
            round(float(chapter["start_time"]) * 24000),
            round(float(chapter["end_time"]) * 24000),
        )
        for chapter in probe["chapters"]
    ] == [(0, 12000), (12000, 24000)]
    assert any(
        stream.get("disposition", {}).get("attached_pic") == 1 for stream in probe["streams"]
    )

    state_index = json.loads(
        (project.state_root / "output" / "state.json").read_text(encoding="utf-8")
    )
    state = state_index["outputs"]["output/profile-book.m4b"]
    assert state["backend"] == "audioexport"
    assert state["options"]["bitrate"] == "128k"
    assert state["metadata"]["cover_sha256"] == hash_file(cover)
    assert Path(state["audioexport_manifest_path"]).is_file()
    output_hash = result["output_sha256"]

    def unexpected_encode(*_args, **_kwargs):
        raise AssertionError("an unchanged profile M4B export should be reused")

    monkeypatch.setattr(audioexport, "encode", unexpected_encode)
    reused = export_audiobook_project(project, profile=profile)
    assert reused["export_id"] == result["export_id"]
    assert reused["output_sha256"] == output_hash


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required for the M4B profile integration test",
)
def test_audiobook_profile_cli_overrides_precede_profile_values(tmp_path: Path):
    pytest.importorskip("audioexport")
    project = _book_project(tmp_path)
    _write_composition(project, starts=(0, 12000), frames=24000)
    profile_cover = _make_image_cover(tmp_path / "profile.png", "blue")
    cli_cover = _make_image_cover(tmp_path / "cli.jpg", "yellow")
    profile = _audioexport_profile(tmp_path / "export.toml", cover=profile_cover, use_cover=False)

    result = export_audiobook_project(
        project,
        profile=profile,
        output=Path("output/cli-book.m4b"),
        title="CLI Title",
        author="CLI Author",
        cover=cli_cover,
        bitrate="96k",
    )

    assert result["path"] == project.state_root / "output" / "cli-book.m4b"
    state_index = json.loads(
        (project.state_root / "output" / "state.json").read_text(encoding="utf-8")
    )
    state = state_index["outputs"]["output/cli-book.m4b"]
    assert state["metadata"]["title"] == "CLI Title"
    assert state["metadata"]["author"] == "CLI Author"
    assert state["metadata"]["cover_sha256"] == hash_file(cli_cover)
    assert state["options"]["bitrate"] == "96k"
    assert state["profile_metadata"]["album"] == "Profile Album"
    assert state["profile_metadata"]["artist"] == "CLI Author"


def test_audiobook_profile_rejects_foreign_timeline_before_replacement(tmp_path: Path):
    pytest.importorskip("audioexport")
    project = _book_project(tmp_path)
    _write_composition(project)
    cover = _png_cover(tmp_path / "profile.png")
    foreign_timeline = tmp_path / "foreign-timeline.json"
    foreign_timeline.write_text(
        project.paths["composition_timeline"].read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    profile = _audioexport_profile(tmp_path / "export.toml", cover=cover, timeline=foreign_timeline)
    target = project.state_root / "output" / "existing.m4b"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"existing user-owned output")

    with pytest.raises(AudiobookExportError) as error:
        export_audiobook_project(project, profile=profile, output=target, force=True)

    assert error.value.code == "audiobook.export.timeline_stale"
    assert target.read_bytes() == b"existing user-owned output"


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required for the M4B profile integration test",
)
def test_audioexport_chapter_verification_failure_preserves_existing_output(tmp_path: Path):
    pytest.importorskip("audioexport")
    project = _book_project(tmp_path)
    _write_composition(project, titles=("Chapter one\ncontinued", "Chapter two"))
    cover = _make_image_cover(tmp_path / "cover.png", "red")
    profile = _audioexport_profile(tmp_path / "export.toml", cover=cover)
    target = project.state_root / "output" / "existing.m4b"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"previous user-owned output")

    with pytest.raises(AudiobookExportError) as error:
        export_audiobook_project(project, profile=profile, output=target, force=True)

    assert error.value.code == "audiobook.export.encode_failed"
    assert target.read_bytes() == b"previous user-owned output"
    assert not (project.state_root / "output" / "state.json").exists()


def test_failed_ffmpeg_preserves_existing_destination_and_state(tmp_path: Path, monkeypatch):
    project = _book_project(tmp_path)
    _write_composition(project)
    output = project.state_root / "output" / "book.m4b"
    output.write_bytes(b"previous valid artifact")

    def failed_run(command, *, capture_output, text, check):
        Path(command[-1]).write_bytes(b"partial output")
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="encoder failed\ninvalid mux",
        )

    monkeypatch.setattr("readio.stages.audiobook_export.ffmpeg_executable", lambda: "ffmpeg")
    monkeypatch.setattr("readio.stages.audiobook_export.subprocess.run", failed_run)
    with pytest.raises(AudiobookExportError) as error:
        export_audiobook_project(project, output=output, force=True)

    assert error.value.code == "audiobook.export.encode_failed"
    assert "encoder failed" in str(error.value)
    assert output.read_bytes() == b"previous valid artifact"
    assert not (project.state_root / "output" / "state.json").exists()
    assert not list((project.state_root / "output").glob("*.ffmeta"))


def test_existing_untracked_output_requires_force(tmp_path: Path):
    project = _book_project(tmp_path)
    _write_composition(project)
    output = tmp_path / "untracked.m4b"
    output.write_bytes(b"user file")

    with pytest.raises(AudiobookExportError) as error:
        export_audiobook_project(project, output=output)

    assert error.value.code == "audiobook.export.output_exists"
    assert output.read_bytes() == b"user file"


def test_missing_ffmpeg_has_specific_error(tmp_path: Path, monkeypatch):
    project = _book_project(tmp_path)
    _write_composition(project)
    monkeypatch.setattr("readio.stages.audiobook_export.ffmpeg_executable", lambda: None)

    with pytest.raises(AudiobookExportError) as error:
        export_audiobook_project(project)

    assert error.value.code == "audiobook.export.ffmpeg_missing"


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required for the M4B profile integration test",
)
def test_saved_audiobook_profile_status_tracks_profile_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audioexport = pytest.importorskip("audioexport")
    if not all(
        hasattr(audioexport, name)
        for name in ("load_profile", "resolve_output", "preflight_profile", "encode")
    ):
        pytest.skip("AudioExport 0.1.1 public profile API is not installed")
    if not audioexport.doctor().get("formats", {}).get("m4b", {}).get("available"):
        pytest.skip("AudioExport M4B encoder is unavailable")

    from readio.api.types import AudiobookExportOptions, ProjectSettings
    from readio.project_settings import with_project_settings
    from readio.stages import pipeline

    project = _book_project(tmp_path)
    _write_composition(project, starts=(0, 12000), frames=24000)
    cover = _make_image_cover(tmp_path / "profile.png", "blue")
    profile = _audioexport_profile(tmp_path / "export.toml", cover=cover)
    exported = export_audiobook_project(project, profile=profile)

    project.paths["synthesis_profile"].parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(project.paths["synthesis_profile"], {"profile_id": "synthesis-id"})
    composition_state = json.loads(project.paths["composition_state"].read_text(encoding="utf-8"))
    composition_state["synthesis_profile_id"] = "synthesis-id"
    atomic_write_json(project.paths["composition_state"], composition_state)
    project.manifest = with_project_settings(
        project.manifest,
        ProjectSettings(audiobook_export=AudiobookExportOptions(profile=profile)),
        project.state_root,
    )
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
        pipeline,
        "_synthesis_status",
        lambda _project: {"stage": "synthesis", "state": "current"},
    )
    monkeypatch.setattr(
        pipeline,
        "build_audio_job",
        lambda *_args, **_kwargs: (None, {"composition_id": "sha256:composition"}),
    )

    current = {row["stage"]: row for row in pipeline.project_status(project)["stages"]}
    assert current["output"]["state"] == "current"
    assert current["output"]["format"] == "m4b"
    assert exported["path"] == project.state_root / "output" / "profile-book.m4b"

    master_before = project.paths["composition_master"].read_bytes()
    _audioexport_profile(profile, cover=cover, title="Changed profile title")
    stale = {row["stage"]: row for row in pipeline.project_status(project)["stages"]}
    assert stale["output"]["state"] == "stale"
    assert stale["output"]["reason"] == "output.stale.project_settings_changed"
    assert project.paths["composition_master"].read_bytes() == master_before
