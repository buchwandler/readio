from __future__ import annotations

import hashlib
import json

import pytest
from audiobook_support import make_epub

from readio.audiobook import init_audiobook_project


def test_init_copies_binary_epub_and_persists_selected_chapters(tmp_path, monkeypatch) -> None:
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
        "document/chapters/chapter-0002.md",
        "document/chapters/chapter-0003.md",
        "document/chapters/chapter-0004.md",
        "document/chapters/chapter-0005.md",
    ]
    assert all(scope.input_format == "markdown" for scope in scopes)
    for scope in scopes:
        markdown = project.path(scope.path).read_text(encoding="utf-8")
        assert markdown.count(f"# {scope.title}") == 1
    index = json.loads(project.paths["document_index"].read_text(encoding="utf-8"))
    assert index["selection"] == [2, 3, 4, 5]
    assert index["metadata"]["title"] == "The Example"
    assert index["metadata"]["authors"] == ["A. Writer"]
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


def test_init_rejects_non_epub_input(tmp_path) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("not an EPUB", encoding="utf-8")

    with pytest.raises(ValueError, match="source is not an EPUB file"):
        init_audiobook_project(source)
