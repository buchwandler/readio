from __future__ import annotations

import json

import pytest
from audiobook_support import make_epub

from readio import cli
from readio.audiobook import inspect_epub


def run_cli(argv: list[str], capsys) -> str:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(argv)
    assert exit_info.value.code == 0
    return capsys.readouterr().out


def test_audiobook_chapters_json_uses_real_epub_extraction(tmp_path, capsys) -> None:
    source = tmp_path / "book.epub"
    make_epub(source)

    payload = json.loads(run_cli(["audiobook", "chapters", str(source), "--json"], capsys))

    assert payload["ok"] is True
    assert payload["metadata"]["title"] == "The Example"
    assert payload["metadata"]["authors"] == ["A. Writer"]
    assert [chapter["number"] for chapter in payload["chapters"]] == [1, 2, 3, 4, 5, 6, 7]
    assert [chapter["title"] for chapter in payload["chapters"]] == [
        "Part One",
        "Chapter One",
        "Chapter Two",
        "Part Two",
        "Chapter Three",
        "Chapter Four",
        "Epilogue",
    ]
    assert any(chapter["level"] > 1 for chapter in payload["chapters"])
    assert all(isinstance(chapter["diagnostics"], list) for chapter in payload["chapters"])
    assert all("ChapterDocument" not in str(chapter) for chapter in payload["chapters"])


def test_audiobook_chapters_human_output_is_compact(tmp_path, capsys) -> None:
    source = tmp_path / "book.epub"
    make_epub(source)

    output = run_cli(["audiobook", "chapters", str(source)], capsys)

    assert "Book: The Example" in output
    assert "Author: A. Writer" in output
    assert "Chapters: 7" in output
    assert "   1 Part One" in output
    assert "   2   Chapter One" in output


def test_synthetic_spine_chapter_receives_flat_number(tmp_path) -> None:
    source = tmp_path / "fallback.epub"
    make_epub(source, with_navigation=False)

    inspection = inspect_epub(source)

    assert len(inspection.chapters) == 5
    assert [chapter.number for chapter in inspection.chapters] == [1, 2, 3, 4, 5]
    assert all(chapter.source_id.startswith("synthetic:") for chapter in inspection.chapters)


def test_malformed_epub_json_error_has_no_partial_chapter_list(tmp_path, capsys) -> None:
    source = tmp_path / "broken.epub"
    source.write_bytes(b"not an epub")

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["audiobook", "chapters", str(source), "--json"])

    assert exit_info.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "could not read EPUB" in payload["error"]
