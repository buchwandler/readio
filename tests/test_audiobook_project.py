from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from audiobook_support import make_epub, make_subset_book_bundle

from readio.api import (
    AudiobookChapter,
    AudiobookInspection,
    InputError,
    InvalidRequestError,
    Readio,
)
from readio.audiobook import init_audiobook_project
from readio.config import ReadioConfig
from readio.project import load_project
from readio.ssmd import parse_ssmd_09


def _assert_project_chapters_are_ssmd(project) -> None:
    for scope in project.document_scopes():
        assert scope.input_format == "ssmd"
        assert scope.path.endswith(".ssmd.md")
        parsed = parse_ssmd_09(project.path(scope.path).read_text(encoding="utf-8"))
        assert parsed.header["title"] == scope.title


def test_init_copies_epub_and_persists_selected_standalone_ssmd(tmp_path, monkeypatch) -> None:
    source = tmp_path / "novel.epub"
    make_epub(source)
    original = source.read_bytes()
    monkeypatch.chdir(tmp_path)

    project = init_audiobook_project(source, chapters="2-4,5")

    assert project.root == tmp_path / "novel.readio"
    assert project.manifest.kind == "audiobook"
    assert project.manifest.source_format == "epub"
    copied = project.paths["source"].read_bytes()
    assert copied == original
    assert hashlib.sha256(copied).hexdigest() == project.manifest.source_sha256
    scopes = project.document_scopes()
    assert [scope.id for scope in scopes] == [
        "chapter-0002",
        "chapter-0003",
        "chapter-0004",
        "chapter-0005",
    ]
    assert [scope.source_number for scope in scopes] == [2, 3, 4, 5]
    assert [scope.title for scope in scopes] == [
        "Chapter One",
        "Chapter Two",
        "Part Two",
        "Chapter Three",
    ]
    assert [scope.path for scope in scopes] == [
        "document/chapters/chapter-0002.ssmd.md",
        "document/chapters/chapter-0003.ssmd.md",
        "document/chapters/chapter-0004.ssmd.md",
        "document/chapters/chapter-0005.ssmd.md",
    ]
    assert scopes[0].parent_id == "chapter-0001"
    assert scopes[0].source_parent_id is not None
    _assert_project_chapters_are_ssmd(project)

    index = json.loads(project.paths["document_index"].read_text(encoding="utf-8"))
    assert index["selection"] == [2, 3, 4, 5]
    assert index["metadata"]["title"] == "The Example"
    assert index["metadata"]["authors"] == ["A. Writer"]
    assert index["metadata"]["conversion"]["tool"] == "ssmdconvert"
    assert index["metadata"]["conversion"]["source_format"] == "epub"
    assert not project.paths["plan_index"].exists()
    assert not project.paths["synthesis_profile"].exists()
    assert source.read_bytes() == original


def test_init_refuses_existing_destination(tmp_path) -> None:
    source = tmp_path / "novel.epub"
    make_epub(source)
    output = tmp_path / "existing.readio"
    output.mkdir()

    with pytest.raises(ValueError, match="project destination already exists"):
        init_audiobook_project(source, output)


@pytest.mark.parametrize(
    ("format", "bundle_name"),
    [("directory", "novel.ssmdbook"), ("zip", "novel.ssmdbook.zip")],
)
def test_bundle_inspection_selection_and_project_creation(
    tmp_path: Path, format: str, bundle_name: str
) -> None:
    source = tmp_path / "novel.epub"
    make_epub(source)
    bundle = tmp_path / bundle_name
    make_subset_book_bundle(source, bundle, format=format)
    app = Readio(ReadioConfig())

    inspection = app.audiobooks.inspect(bundle)
    assert isinstance(inspection, AudiobookInspection)
    assert inspection.source == bundle.resolve()
    assert [chapter.number for chapter in inspection.chapters] == [2, 3, 4, 7]
    assert all(isinstance(chapter, AudiobookChapter) for chapter in inspection.chapters)
    assert all(not hasattr(chapter, "markdown") for chapter in inspection.chapters)
    assert inspection.chapters[0].source_parent_id is not None

    created = app.audiobooks.create_project_result(bundle, output=tmp_path / "all.readio")
    project = load_project(created.project.root)
    assert created.selected_chapters == 4
    assert [chapter.number for chapter in created.chapters] == [2, 3, 4, 7]
    assert project.manifest.source_format == "ssmdbook"
    assert project.paths["source"].name == "novel.ssmdbook.zip"
    assert (
        project.manifest.source_sha256
        == hashlib.sha256(project.paths["source"].read_bytes()).hexdigest()
    )
    assert [scope.id for scope in project.document_scopes()] == [
        "chapter-0002",
        "chapter-0003",
        "chapter-0004",
        "chapter-0007",
    ]
    assert [scope.source_number for scope in project.document_scopes()] == [2, 3, 4, 7]
    assert project.load_document_index().metadata["title"] == "The Example"
    assert project.load_document_index().metadata["conversion"]["source_format"] == "ssmdbook"
    _assert_project_chapters_are_ssmd(project)
    assert app.projects.status(project.root).stage("source").state == "current"

    selected = app.audiobooks.create_project_result(
        bundle, chapters="3-4", output=tmp_path / "selected.readio"
    )
    assert [chapter.number for chapter in selected.chapters] == [3, 4]
    with pytest.raises(InvalidRequestError) as error:
        app.audiobooks.create_project(bundle, chapters="5", output=tmp_path / "invalid.readio")
    assert error.value.code == "request.chapter_selection_invalid"
    assert error.value.source_path == bundle.resolve()

    if format == "directory":
        second = app.audiobooks.create_project_result(bundle, output=tmp_path / "second.readio")
        assert (second.project.root / "source" / "novel.ssmdbook.zip").read_bytes() == (
            project.root / "source" / "novel.ssmdbook.zip"
        ).read_bytes()


def test_book_source_errors_have_specific_api_codes(tmp_path) -> None:
    app = Readio(ReadioConfig())
    unsupported = tmp_path / "notes.txt"
    unsupported.write_text("not a book", encoding="utf-8")
    with pytest.raises(InputError) as unsupported_error:
        app.audiobooks.inspect(unsupported)
    assert unsupported_error.value.code == "input.book_format_unsupported"
    assert unsupported_error.value.source_path == unsupported.resolve()

    missing = tmp_path / "missing.epub"
    with pytest.raises(InputError) as missing_error:
        app.audiobooks.inspect(missing)
    assert missing_error.value.code == "input.not_found"
    assert missing_error.value.source_path == missing.resolve()

    malformed = tmp_path / "broken.ssmdbook.zip"
    malformed.write_bytes(b"not a book bundle")
    with pytest.raises(InputError) as bundle_error:
        app.audiobooks.inspect(malformed)
    assert bundle_error.value.code == "input.book_bundle_invalid"
    assert bundle_error.value.source_path == malformed.resolve()
