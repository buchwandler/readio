from __future__ import annotations

from pathlib import Path

from audiobook_support import make_epub
from readio.audiobook import init_audiobook_project
from readio.integrations.ssmdconvert import load_book_workspace_source


def test_compatibility_initializer_creates_native_ssmdbook_only(tmp_path: Path) -> None:
    source = tmp_path / "novel.epub"
    make_epub(source)
    workspace_path = tmp_path / "novel.ssmdbook"

    workspace = init_audiobook_project(source, workspace_path, chapters="2-3")

    assert workspace.root == workspace_path
    native = load_book_workspace_source(workspace_path)
    assert [chapter.source_number for chapter in native.chapters] == [2, 3]
    assert not (workspace_path / ".readio").exists()
    assert not (workspace_path / "project.json").exists()


def test_existing_workspace_initializer_is_read_only(tmp_path: Path) -> None:
    source = tmp_path / "novel.epub"
    make_epub(source)
    workspace_path = tmp_path / "novel.ssmdbook"
    from ssmdconvert import convert_book, write_book_bundle

    book = convert_book(source).book
    write_book_bundle(book, workspace_path, format="directory")
    before = {
        path.relative_to(workspace_path): path.read_bytes()
        for path in workspace_path.rglob("*")
        if path.is_file()
    }

    workspace = init_audiobook_project(workspace_path)

    after = {
        path.relative_to(workspace_path): path.read_bytes()
        for path in workspace_path.rglob("*")
        if path.is_file()
    }
    assert workspace.root == workspace_path
    assert after == before
    assert not (workspace_path / ".readio").exists()
