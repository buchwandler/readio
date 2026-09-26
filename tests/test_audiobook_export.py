from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from audiobook_support import make_epub

from readio.audiobook import init_audiobook_project
from readio.project import atomic_write_json, hash_file, init_project
from readio.stages.audiobook_export import (
    AudiobookExportError,
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
    return init_audiobook_project(source, tmp_path / "book.readio")


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
    output = project.root / "output" / "finished"

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
    state_index = json.loads((project.root / "output" / "state.json").read_text(encoding="utf-8"))
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


def test_failed_ffmpeg_preserves_existing_destination_and_state(tmp_path: Path, monkeypatch):
    project = _book_project(tmp_path)
    _write_composition(project)
    output = project.root / "output" / "book.m4b"
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
    assert not (project.root / "output" / "state.json").exists()
    assert not list((project.root / "output").glob("*.ffmeta"))


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
