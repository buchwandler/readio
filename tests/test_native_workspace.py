from __future__ import annotations

from pathlib import Path

import pytest
import ssmdconvert
from ebooklib import epub

from readio.workspace import CreateWorkspaceRequest, create_workspace, resolve_workspace


def _make_epub(path: Path) -> None:
    book = epub.EpubBook()
    book.set_identifier("workspace-test")
    book.set_title("Workspace Test")
    book.set_language("en")

    first = epub.EpubHtml(title="One", file_name="chapter-1.xhtml", lang="en")
    first.content = "<html><body><h1>One</h1><p>First chapter.</p></body></html>"
    second = epub.EpubHtml(title="Two", file_name="chapter-2.xhtml", lang="en")
    second.content = "<html><body><h1>Two</h1><p>Second chapter.</p></body></html>"
    book.add_item(first)
    book.add_item(second)
    book.add_item(epub.EpubNav())
    book.add_item(epub.EpubNcx())
    book.toc = (
        epub.Link("chapter-1.xhtml", "One", "one"),
        epub.Link("chapter-2.xhtml", "Two", "two"),
    )
    book.spine = ["nav", first, second]
    epub.write_epub(str(path), book)


def test_create_and_resolve_native_document_workspace(tmp_path: Path) -> None:
    source = tmp_path / "story.txt"
    source.write_text("A short story.", encoding="utf-8")

    workspace = create_workspace(source)

    assert workspace.kind == "document"
    assert workspace.root == (tmp_path / "story.ssmdoc").resolve()
    assert workspace.document_id == "document-0001"
    assert (workspace.root / "manifest.json").is_file()
    assert (workspace.root / "document.ssmd.md").is_file()
    assert not (workspace.root / ".readio").exists()
    assert resolve_workspace(workspace.root) == workspace


def test_create_native_document_from_valid_ssmd_source(tmp_path: Path) -> None:
    source = tmp_path / "article.ssmd.md"
    source.write_text(
        '---\nssmd_version: "0.9"\ntitle: "Article"\nlanguage: en\n---\n\nHello.\n',
        encoding="utf-8",
    )

    workspace = create_workspace(source)

    assert workspace.root.name == "article.ssmdoc"
    assert workspace.kind == "document"


def test_create_selected_book_workspace_with_package_chapter_ids(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    _make_epub(source)

    workspace = create_workspace(
        source,
        CreateWorkspaceRequest(source=source, chapters="2"),
    )

    native = ssmdconvert.load_book_workspace(workspace.root)
    assert workspace.kind == "book"
    assert workspace.root == (tmp_path / "book.ssmdbook").resolve()
    assert workspace.chapter_ids == ("chapter-0002",)
    assert tuple(chapter.id for chapter in native.book.chapters) == workspace.chapter_ids
    assert (workspace.root / "manifest.json").is_file()
    assert not (workspace.root / ".readio").exists()


def test_existing_native_workspace_is_used_without_reconversion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "story.txt"
    source.write_text("A short story.", encoding="utf-8")
    created = create_workspace(source)

    def unexpected_conversion(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("existing workspace must not be reconverted")

    monkeypatch.setattr(ssmdconvert, "convert_document_artifact", unexpected_conversion)
    assert create_workspace(created.root) == created


def test_strict_suffix_missing_path_and_kind_mismatch_errors(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"must end in \.ssmdoc or \.ssmdbook"):
        resolve_workspace(tmp_path / "not-a-workspace")

    with pytest.raises(FileNotFoundError):
        resolve_workspace(tmp_path / "missing.ssmdoc")

    source = tmp_path / "story.txt"
    source.write_text("A short story.", encoding="utf-8")
    with pytest.raises(ValueError, match=r"cannot end in \.ssmdbook"):
        create_workspace(
            source,
            CreateWorkspaceRequest(source=source, output=tmp_path / "wrong.ssmdbook"),
        )


def test_package_loader_exception_is_propagated_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "malformed.ssmdoc"
    root.mkdir()
    failure = RuntimeError("invalid native manifest")

    def fail_loader(_path: Path) -> None:
        raise failure

    monkeypatch.setattr(ssmdconvert, "load_document_workspace", fail_loader)

    with pytest.raises(RuntimeError) as caught:
        resolve_workspace(root)

    assert caught.value is failure
