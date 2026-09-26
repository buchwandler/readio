from __future__ import annotations

import json

import pytest
from audiobook_support import make_epub

from readio import cli
from readio.api import AudiobookExportResult, ProjectExportResult, ProjectRef
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


def test_audiobook_export_cli_uses_public_api_options(tmp_path, capsys, monkeypatch) -> None:
    project_path = tmp_path / "book.readio"
    output = tmp_path / "custom.m4b"
    cover = tmp_path / "cover.png"
    project = ProjectRef(
        root=project_path,
        project_id="project-id",
        name="book",
        kind="audiobook",
        source_format="epub",
    )
    result = AudiobookExportResult(
        project=project,
        output_path=output,
        format="m4b",
        output_sha256="sha256:output",
        export_id="sha256:export",
        chapter_count=7,
    )
    captured = {}

    class FakeAudiobooks:
        def export(self, project_argument, options):
            captured["project"] = project_argument
            captured["options"] = options
            return result

    class FakeApp:
        audiobooks = FakeAudiobooks()

    monkeypatch.setattr(cli, "_api_for", lambda args: FakeApp())
    payload = json.loads(
        run_cli(
            [
                "audiobook",
                "export",
                "--format",
                "m4b",
                str(project_path),
                "--title",
                "Book title",
                "--author",
                "Book author",
                "--cover",
                str(cover),
                "--bitrate",
                "96k",
                "--output",
                str(output),
                "--force",
                "--json",
            ],
            capsys,
        )
    )

    assert payload["ok"] is True
    assert payload["format"] == "m4b"
    assert payload["chapter_count"] == 7
    assert captured["project"] == project_path
    options = captured["options"]
    assert options.format == "m4b"
    assert options.title == "Book title"
    assert options.author == "Book author"
    assert options.cover == cover
    assert options.bitrate == "96k"
    assert options.output == output
    assert options.force is True


def test_export_cli_has_generic_flac_opus_but_not_m4b(tmp_path, capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["export", "--help"])
    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "flac" in help_text
    assert "opus" in help_text
    assert "m4b" not in help_text

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["audiobook", "export", "--help"])
    assert exit_info.value.code == 0
    audiobook_help = capsys.readouterr().out
    assert "--cover" in audiobook_help
    assert "M4B output path" in audiobook_help


def test_generic_export_cli_forwards_flac_format_and_force(tmp_path, capsys, monkeypatch):
    project_path = tmp_path / "book.readio"
    output = tmp_path / "book.flac"
    project = ProjectRef(
        root=project_path,
        project_id="project-id",
        name="book",
        kind="document",
        source_format="epub",
    )
    result = ProjectExportResult(
        project=project,
        output_path=output,
        format="flac",
        output_sha256="sha256:output",
        export_id="sha256:export",
    )
    captured = {}

    class FakeProjects:
        def export(self, project_argument, options):
            captured["project"] = project_argument
            captured["options"] = options
            return result

    class FakeApp:
        projects = FakeProjects()

    monkeypatch.setattr(cli, "_api_for", lambda args: FakeApp())
    payload = json.loads(
        run_cli(
            [
                "export",
                str(project_path),
                "--format",
                "flac",
                "--bitrate",
                "320k",
                "--output",
                str(output),
                "--force",
                "--json",
            ],
            capsys,
        )
    )

    assert payload["format"] == "flac"
    assert captured["project"] == project_path
    options = captured["options"]
    assert options.format == "flac"
    assert options.bitrate == "320k"
    assert options.output == output
    assert options.force is True
