from __future__ import annotations

import hashlib
import shutil
import zipfile
from pathlib import Path

import pytest
from audiobook_support import make_epub, make_subset_book_bundle
from ssmdconvert import validate_book_bundle

from readio.api import (
    AudiobookChapter,
    AudiobookExportOptions,
    AudiobookInspection,
    InputError,
    InvalidRequestError,
    ProjectConflictError,
    ProjectSettings,
    Readio,
)
from readio.audiobook import init_audiobook_project
from readio.config import ReadioConfig
from readio.integrations.ssmdconvert import (
    convert_book_source,
    load_book_workspace_source,
    write_book_bundle,
)
from readio.project import load_project
from readio.ssmd import parse_ssmd_09


def _assert_project_chapters_are_ssmd(project) -> None:
    for scope in project.document_scopes():
        assert scope.input_format == "ssmd"
        assert scope.path.endswith(".ssmd.md")
        parsed = parse_ssmd_09(project.load_document_scope(scope).text)
        assert parsed.header["title"] == scope.title


def test_init_materializes_full_editable_workspace_and_persists_render_selection(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "novel.epub"
    make_epub(source)
    original = source.read_bytes()
    monkeypatch.chdir(tmp_path)

    project = init_audiobook_project(source, chapters="2-4,5")
    workspace_book = load_book_workspace_source(project.workspace_root)

    assert project.root == tmp_path / "novel.ssmdbook"
    assert project.root == project.workspace_root
    assert project.state_root == project.root / ".readio"
    assert project.manifest.kind == "audiobook"
    assert project.manifest.source_format == "ssmdbook"
    assert project.manifest.schema_version == 4
    assert project.paths["source"] == project.root / "manifest.json"
    assert (
        project.manifest.source_sha256
        == hashlib.sha256(project.paths["source"].read_bytes()).hexdigest()
    )
    assert len(workspace_book.chapters) == 7
    assert [scope.id for scope in project.document_scopes()] == [
        "chapter-0002",
        "chapter-0003",
        "chapter-0004",
        "chapter-0005",
    ]
    assert [scope.source_number for scope in project.document_scopes()] == [2, 3, 4, 5]
    assert [scope.path for scope in project.document_scopes()] == [
        workspace_book.chapter_paths[chapter.id]
        for chapter in workspace_book.chapters
        if chapter.source_number in {2, 3, 4, 5}
    ]
    assert project.load_document_index().selection == (2, 3, 4, 5)
    assert project.load_document_index().metadata["title"] == "The Example"
    assert project.load_document_index().metadata["authors"] == ["A. Writer"]
    assert project.load_document_index().metadata["conversion"]["tool"] == "ssmdconvert"
    assert project.load_document_index().metadata["conversion"]["source_format"] == "ssmdbook"
    assert not (project.state_root / "source").exists()
    assert not (project.state_root / "document" / "chapters").exists()
    assert all((project.root / scope.path).is_file() for scope in project.document_scopes())
    _assert_project_chapters_are_ssmd(project)
    assert source.read_bytes() == original
    assert not tuple(tmp_path.glob(".novel.tmp-*.ssmdbook"))


def test_epub_language_and_zip_materialization_preserve_source_and_pack_only_book_data(
    tmp_path: Path,
) -> None:
    source = tmp_path / "localized.epub"
    make_epub(source)
    original_source = source.read_bytes()
    localized_root = tmp_path / "localized.ssmdbook"
    localized = init_audiobook_project(source, localized_root, chapters="2-3", language="de-DE")
    canonical = load_book_workspace_source(localized_root)

    assert localized.root == localized_root
    assert len(canonical.chapters) == 7
    assert canonical.metadata["language"] == "de-DE"
    assert [scope.source_number for scope in localized.document_scopes()] == [2, 3]
    assert localized.load_document_index().selection == (2, 3)
    assert canonical.workspace_dirty is False
    validate_book_bundle(localized_root)
    assert source.read_bytes() == original_source

    source_zip = tmp_path / "source.ssmdbook.zip"
    write_book_bundle(canonical, source_zip, format="zip")
    original_zip = source_zip.read_bytes()
    zip_root = tmp_path / "from-zip.ssmdbook"
    from_zip = init_audiobook_project(source_zip, zip_root, chapters="2-3")
    zip_book = load_book_workspace_source(zip_root)
    assert len(zip_book.chapters) == 7
    assert [scope.source_number for scope in from_zip.document_scopes()] == [2, 3]
    assert zip_book.workspace_dirty is False
    validate_book_bundle(zip_root)
    assert source_zip.read_bytes() == original_zip

    portable_zip = tmp_path / "portable.ssmdbook.zip"
    write_book_bundle(load_book_workspace_source(localized_root), portable_zip, format="zip")
    with zipfile.ZipFile(portable_zip) as archive:
        assert all(".readio" not in name for name in archive.namelist())

    with pytest.raises(ValueError, match="only supported for EPUB"):
        init_audiobook_project(source_zip, tmp_path / "unsupported.ssmdbook", language="fr-FR")
    with pytest.raises(ValueError, match=".ssmdbook suffix"):
        init_audiobook_project(source, tmp_path / "bad.readio")
    assert not (tmp_path / "unsupported.ssmdbook").exists()
    assert not (tmp_path / "bad.readio").exists()
    assert not tuple(tmp_path.glob(".localized.tmp-*.ssmdbook"))
    assert not tuple(tmp_path.glob(".from-zip.tmp-*.ssmdbook"))


def test_init_refuses_existing_destination(tmp_path) -> None:
    source = tmp_path / "novel.epub"
    make_epub(source)
    output = tmp_path / "existing.ssmdbook"
    output.mkdir()

    with pytest.raises(ValueError, match="workspace destination already exists"):
        init_audiobook_project(source, output)


@pytest.mark.parametrize(
    ("format", "bundle_name"),
    [("directory", "novel.ssmdbook"), ("zip", "novel.ssmdbook.zip")],
)
def test_bundle_inspection_selection_and_project_creation(
    tmp_path: Path, monkeypatch, format: str, bundle_name: str
) -> None:
    source = tmp_path / "novel.epub"
    make_epub(source)
    bundle = tmp_path / bundle_name
    make_subset_book_bundle(source, bundle, format=format)
    monkeypatch.chdir(tmp_path)
    source_bundle_bytes = bundle.read_bytes() if bundle.is_file() else None
    app = Readio(ReadioConfig())

    inspection = app.audiobooks.inspect(bundle)
    assert isinstance(inspection, AudiobookInspection)
    assert inspection.source == bundle.resolve()
    assert [chapter.number for chapter in inspection.chapters] == [2, 3, 4, 7]
    assert all(isinstance(chapter, AudiobookChapter) for chapter in inspection.chapters)
    assert all(not hasattr(chapter, "markdown") for chapter in inspection.chapters)
    assert inspection.chapters[0].source_parent_id is not None
    if format == "directory":
        assert not (bundle / ".readio").exists()

    created = app.audiobooks.create_project_result(bundle)
    if source_bundle_bytes is not None:
        assert bundle.read_bytes() == source_bundle_bytes
    assert created.project.root == (
        bundle if format == "directory" else tmp_path / "novel.ssmdbook"
    )
    project = load_project(created.project.root)
    assert created.selected_chapters == 4
    assert [chapter.number for chapter in created.chapters] == [2, 3, 4, 7]
    assert project.manifest.source_format == "ssmdbook"
    assert project.paths["source"].name == "manifest.json"
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

    selected_bundle = bundle
    invalid_bundle = bundle
    if format == "directory":
        selected_bundle = tmp_path / "selected.ssmdbook"
        make_subset_book_bundle(source, selected_bundle, format="directory")
        invalid_bundle = tmp_path / "invalid.ssmdbook"
        make_subset_book_bundle(source, invalid_bundle, format="directory")
    selected = (
        app.audiobooks.create_project_result(selected_bundle, chapters="3-4")
        if format == "directory"
        else app.audiobooks.create_project_result(
            selected_bundle, chapters="3-4", output=tmp_path / "selected.ssmdbook"
        )
    )
    assert [chapter.number for chapter in selected.chapters] == [3, 4]
    with pytest.raises(InvalidRequestError) as error:
        if format == "directory":
            app.audiobooks.create_project(invalid_bundle, chapters="5")
        else:
            app.audiobooks.create_project(
                invalid_bundle, chapters="5", output=tmp_path / "invalid.ssmdbook"
            )
    assert error.value.code == "request.chapter_selection_invalid"
    assert error.value.source_path == invalid_bundle.resolve()


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


def test_existing_workspace_attaches_in_place_and_rebuilds_disposable_state(tmp_path: Path) -> None:
    epub = tmp_path / "novel.epub"
    make_epub(epub)
    workspace = tmp_path / "novel.ssmdbook"
    write_book_bundle(convert_book_source(epub), workspace, format="directory")
    source_files = {
        path.relative_to(workspace): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    }
    app = Readio(ReadioConfig())

    settings = ProjectSettings(
        audiobook_export=AudiobookExportOptions(
            output=workspace / ".readio" / "output" / "selected.m4b"
        )
    )
    created = app.audiobooks.create_project_result(workspace, chapters="2-3", settings=settings)
    assert app.projects.settings(created.project) == settings
    project = load_project(created.project.root)
    scopes = project.document_scopes()
    canonical_book = load_book_workspace_source(workspace)

    assert not tuple(workspace.glob(".readio.tmp-*"))
    assert created.project.root == workspace.resolve()
    assert project.root == project.workspace_root == workspace.resolve()
    assert project.state_root == workspace / ".readio"
    assert project.manifest.schema_version == 4
    assert project.manifest.layout_mode == "attached-ssmdbook"
    assert project.paths["source"] == workspace / "manifest.json"
    assert [scope.source_number for scope in scopes] == [2, 3]
    assert project.load_document_index().selection == (2, 3)
    assert [scope.path for scope in scopes] == [
        canonical_book.chapter_paths[chapter.id]
        for chapter in canonical_book.chapters
        if chapter.source_number in {2, 3}
    ]
    assert all((workspace / scope.path).is_file() for scope in scopes)
    assert not (project.state_root / "document" / "chapters").exists()
    assert app.audiobooks.describe_project(created.project).source == workspace / "manifest.json"
    assert {
        path.relative_to(workspace): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file() and ".readio" not in path.parts
    } == source_files

    with pytest.raises(ProjectConflictError, match="already exists"):
        app.audiobooks.create_project(workspace)
    with pytest.raises(InvalidRequestError, match="--output"):
        app.audiobooks.create_project(workspace, output=tmp_path / "copy.ssmdbook")
    with pytest.raises(InvalidRequestError, match="only supported for EPUB"):
        app.audiobooks.create_project(workspace, language="fr-FR")

    shutil.rmtree(project.state_root)
    assert not project.state_root.exists()
    recreated = app.audiobooks.create_project_result(workspace, chapters="2-3")
    assert recreated.project.root == workspace.resolve()
    assert recreated.project.project_id == created.project.project_id
    assert load_project(workspace).load_document_index().selection == (2, 3)
    assert {
        path.relative_to(workspace): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file() and ".readio" not in path.parts
    } == source_files
