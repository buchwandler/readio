from pathlib import Path

from readio.document import document_from_file, document_from_stdin, document_from_text


def test_document_from_ssmd_file_classifies_by_suffix(tmp_path: Path):
    source = tmp_path / "episode.ssmd"
    source.write_text("plain text", encoding="utf-8")

    document = document_from_file(source)

    assert document.text == "plain text"
    assert document.source_path == source
    assert document.format == "ssmd"


def test_document_from_other_file_and_literals_are_plain_text(tmp_path: Path):
    source = tmp_path / "episode.md"
    source.write_text("---\nvoice_bindings: nope\n---", encoding="utf-8")

    assert document_from_file(source).format == "text"
    assert document_from_text("---\nvoice_bindings: nope\n---").format == "text"
    assert document_from_stdin('<div voice="host">Hello</div>').format == "text"
