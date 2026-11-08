from __future__ import annotations

from pathlib import Path

import pytest
from ebooklib import epub

from readio.api import Readio, SourceChapter, SourceInspection, inspect_source
from readio.errors import ReadioError


def _make_epub(path: Path) -> None:
    book = epub.EpubBook()
    book.set_identifier("source-inspection-test")
    book.set_title("Source Inspection Test")
    book.add_author("Ada Lovelace")
    book.set_language("en")

    first = epub.EpubHtml(title="Opening", file_name="opening.xhtml", lang="en")
    first.content = "<html><body><h1>Opening</h1><p>First chapter.</p></body></html>"
    second = epub.EpubHtml(title="Conclusion", file_name="conclusion.xhtml", lang="en")
    second.content = "<html><body><h1>Conclusion</h1><p>Last chapter.</p></body></html>"
    book.add_item(first)
    book.add_item(second)
    book.add_item(epub.EpubNav())
    book.add_item(epub.EpubNcx())
    book.toc = (
        epub.Link(first.file_name, first.title, "opening"),
        epub.Link(second.file_name, second.title, "conclusion"),
    )
    book.spine = ["nav", first, second]
    epub.write_epub(str(path), book)


def test_inspect_source_returns_normalized_book_metadata_and_ordered_chapters(
    tmp_path: Path,
) -> None:
    source = tmp_path / "novel.epub"
    _make_epub(source)

    inspection = inspect_source(source)

    assert isinstance(inspection, SourceInspection)
    assert inspection.source == source.resolve()
    assert inspection.kind == "book"
    assert inspection.metadata["title"] == "Source Inspection Test"
    assert inspection.metadata["authors"] == ["Ada Lovelace"]
    assert inspection.metadata["language"] == "en"
    assert [(chapter.number, chapter.title) for chapter in inspection.chapters] == [
        (1, "Opening"),
        (2, "Conclusion"),
    ]
    assert all(isinstance(chapter, SourceChapter) for chapter in inspection.chapters)
    assert all(chapter.source_id for chapter in inspection.chapters)
    assert [chapter.level for chapter in inspection.chapters] == [1, 1]
    assert all(chapter.char_count is not None for chapter in inspection.chapters)


def test_readio_source_inspection_method_uses_public_normalized_result(tmp_path: Path) -> None:
    source = tmp_path / "article.ssmd.md"
    source.write_text(
        '---\nssmd_version: "0.9"\ntitle: "Article"\nlanguage: en\n---\n\nHello.\n',
        encoding="utf-8",
    )

    inspection = Readio().inspect_source(source)

    assert isinstance(inspection, SourceInspection)
    assert inspection.kind == "document"
    assert inspection.metadata["title"] == "Article"
    assert inspection.metadata["language"] == "en"
    assert inspection.chapters == ()


def test_inspect_source_does_not_create_workspace_or_mutate_source(tmp_path: Path) -> None:
    source = tmp_path / "novel.epub"
    _make_epub(source)
    original = source.read_bytes()
    entries_before = {entry.name for entry in tmp_path.iterdir()}

    inspection = inspect_source(source)

    assert inspection.kind == "book"
    assert source.read_bytes() == original
    assert {entry.name for entry in tmp_path.iterdir()} == entries_before
    assert not (tmp_path / "novel.ssmd-bundle").exists()


def test_inspect_source_wraps_missing_and_unsupported_sources(tmp_path: Path) -> None:
    with pytest.raises(ReadioError, match="Source not found"):
        inspect_source(tmp_path / "missing.epub")

    unsupported = tmp_path / "source.pdf"
    unsupported.write_bytes(b"not a supported Readio source")
    with pytest.raises(ReadioError):
        inspect_source(unsupported)
