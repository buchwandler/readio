from __future__ import annotations

from pathlib import Path

import pytest
from audiobook_support import make_epub, make_subset_book_bundle
from ssmdconvert import MissingDependencyError

from readio.audiobook import init_audiobook_project
from readio.errors import InputError
from readio.integrations import ssmdconvert as integration
from readio.integrations.ssmdconvert import (
    BookInputError,
    BookInspection,
    BookSelectionError,
    CanonicalBook,
    CanonicalDocument,
    MissingInputDependencyError,
    convert_book_source,
    convert_document_content,
    convert_document_source,
    inspect_book_source,
    load_book_bundle_source,
    load_book_workspace_source,
    write_book_bundle,
)


def test_document_conversion_returns_readio_dto_and_translates_missing_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("Plain source text.", encoding="utf-8")

    document = convert_document_source(source)

    assert isinstance(document, CanonicalDocument)
    assert document.source == source.resolve()
    assert document.source_name == source.name
    assert document.source_format == "text"
    assert document.ssmd
    assert document.converter_version

    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"placeholder")

    def missing_parser(_source: Path):
        raise MissingDependencyError("install the PDF extra")

    monkeypatch.setattr(integration, "ssmdconvert_convert", missing_parser)
    with pytest.raises(MissingInputDependencyError) as missing:
        convert_document_source(pdf)
    assert missing.value.code == "input.converter_dependency_missing"
    assert missing.value.source_path == pdf.resolve()


def test_in_memory_document_conversion_returns_readio_dto(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = convert_document_content(
        "# Notes\n\nA paragraph.", input_format="markdown", source_name="notes.md"
    )

    assert isinstance(document, CanonicalDocument)
    assert document.source is None
    assert document.source_format == "markdown"
    assert document.source_name == "notes.md"
    assert document.ssmd

    source = tmp_path / "notes.md"

    def missing_parser(*_args, **_kwargs):
        raise MissingDependencyError("install the parser extra")

    monkeypatch.setattr(integration, "ssmdconvert_convert_content", missing_parser)
    with pytest.raises(MissingInputDependencyError) as missing:
        convert_document_content(
            "# Notes", input_format="markdown", source_name=source.name, source_path=source
        )
    assert missing.value.source_path == source


def test_book_conversion_inspection_and_bundle_round_trip_use_readio_dtos(
    tmp_path: Path,
) -> None:
    epub = tmp_path / "novel.epub"
    make_epub(epub)

    book = convert_book_source(epub, chapters="2-3")
    inspection = inspect_book_source(epub)

    assert isinstance(book, CanonicalBook)
    assert book.source_format == "epub"
    assert book.source_name == epub.name
    assert book.metadata["title"] == "The Example"
    assert [chapter.source_number for chapter in book.chapters] == [2, 3]
    assert isinstance(inspection, BookInspection)
    assert inspection.source == epub.resolve()
    assert inspection.source_format == "epub"
    assert inspection.metadata["title"] == "The Example"
    assert [chapter.number for chapter in inspection.chapters] == [1, 2, 3, 4, 5, 6, 7]

    source_bundle = tmp_path / "source.ssmdbook.zip"
    make_subset_book_bundle(epub, source_bundle, format="zip")
    canonical_bundle = load_book_bundle_source(source_bundle)
    assert isinstance(canonical_bundle, CanonicalBook)
    assert canonical_bundle.source_format == "epub"

    copied_bundle = tmp_path / "copy.ssmdbook.zip"
    write_book_bundle(canonical_bundle, copied_bundle, format="zip")
    copied = load_book_bundle_source(copied_bundle)
    assert copied.metadata == canonical_bundle.metadata
    assert [chapter.source_number for chapter in copied.chapters] == [2, 3, 4, 7]


def test_book_boundary_translates_unsupported_bundle_and_selection_errors(
    tmp_path: Path,
) -> None:
    unsupported = tmp_path / "notes.txt"
    unsupported.write_text("not a book", encoding="utf-8")
    with pytest.raises(BookInputError) as unsupported_error:
        inspect_book_source(unsupported)
    assert unsupported_error.value.code == "input.book_format_unsupported"

    invalid_bundle = tmp_path / "broken.ssmdbook.zip"
    invalid_bundle.write_bytes(b"not a bundle")
    with pytest.raises(BookInputError) as bundle_error:
        inspect_book_source(invalid_bundle)
    assert bundle_error.value.code == "input.book_bundle_invalid"
    assert bundle_error.value.source_path == invalid_bundle.resolve()

    epub = tmp_path / "novel.epub"
    make_epub(epub)
    with pytest.raises(BookSelectionError) as selection_error:
        convert_book_source(epub, chapters="99")
    assert selection_error.value.code == "input.book_selection_invalid"
    assert selection_error.value.source_path == epub.resolve()

    bundle = tmp_path / "novel.ssmdbook.zip"
    make_subset_book_bundle(epub, bundle, format="zip")
    with pytest.raises(BookSelectionError) as bundle_selection_error:
        init_audiobook_project(bundle, tmp_path / "invalid.ssmdbook", chapters="5")
    assert bundle_selection_error.value.code == "input.book_selection_invalid"
    assert bundle_selection_error.value.source_path == bundle.resolve()


def test_document_boundary_translates_unsupported_input(tmp_path: Path) -> None:
    source = tmp_path / "unsupported.unknown"
    source.write_text("unsupported", encoding="utf-8")

    with pytest.raises(InputError) as error:
        convert_document_source(source)

    assert error.value.code == "input.format_unsupported"
    assert error.value.source_path == source.resolve()


def test_workspace_loader_uses_current_chapter_bytes_and_reports_dirty_digests(
    tmp_path: Path,
) -> None:
    epub = tmp_path / "novel.epub"
    make_epub(epub)
    workspace = tmp_path / "novel.ssmdbook"
    write_book_bundle(convert_book_source(epub), workspace, format="directory")

    clean = load_book_workspace_source(workspace)
    assert clean.workspace_dirty is False
    assert clean.dirty_chapter_ids == ()
    assert set(clean.chapter_paths) == {chapter.id for chapter in clean.chapters}

    changed_id = clean.chapters[0].id
    changed_path = workspace / clean.chapter_paths[changed_id]
    original_manifest = (workspace / "manifest.json").read_bytes()
    changed_path.write_text(
        changed_path.read_text(encoding="utf-8") + "\n\nAn unsynchronized author edit.\n",
        encoding="utf-8",
    )
    dirty = load_book_workspace_source(workspace)

    assert dirty.workspace_dirty is True
    assert dirty.dirty_chapter_ids == (changed_id,)
    assert dirty.chapters[0].ssmd.endswith("An unsynchronized author edit.\n")
    assert (workspace / "manifest.json").read_bytes() == original_manifest
