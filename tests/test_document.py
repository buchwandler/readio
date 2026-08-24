from pathlib import Path

from readio.document import document_from_file, document_from_stdin, document_from_text


def test_document_from_ssmd_file_classifies_by_suffix(tmp_path: Path):
    source = tmp_path / "episode.ssmd"
    source.write_text("plain text", encoding="utf-8")

    document = document_from_file(source)

    assert document.text == "plain text"
    assert document.source_path == source
    assert document.format == "ssmd"


def test_document_from_markdown_suffixes_and_literals(tmp_path: Path):
    for suffix in (".md", ".markdown", ".mdown", ".mkd", ".MD"):
        source = tmp_path / f"episode{suffix}"
        source.write_text("# heading", encoding="utf-8")
        assert document_from_file(source).format == "markdown"

    assert document_from_text("# heading").format == "text"
    assert document_from_text("# heading", input_format="markdown").format == "markdown"
    assert document_from_stdin("# heading", input_format="markdown").format == "markdown"


def test_document_from_other_suffix_and_explicit_overrides(tmp_path: Path):
    source = tmp_path / "episode.md"
    source.write_text("# heading", encoding="utf-8")

    assert document_from_file(source, input_format="text").format == "text"
    assert document_from_file(source, input_format="ssmd").format == "ssmd"
    other = tmp_path / "episode.txt"
    other.write_text("plain", encoding="utf-8")
    assert document_from_file(other).format == "text"
