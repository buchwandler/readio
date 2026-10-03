from pathlib import Path

from readio.document import document_from_file, document_from_stdin, document_from_text

_SSMD = "---\nssmd_version: '0.9'\n---\nHello.\n"


def test_document_from_ssmd_file_uses_conversion_and_preserves_source(tmp_path: Path):
    source = tmp_path / "episode.ssmd"
    source.write_text(_SSMD, encoding="utf-8")

    document = document_from_file(source)

    assert document.text
    assert document.source_path == source.resolve()
    assert document.format == "ssmd"


def test_ssmd_markdown_compound_extension_is_converted(tmp_path: Path):
    source = tmp_path / "episode.SSMD.MD"
    source.write_text(_SSMD, encoding="utf-8")

    assert document_from_file(source).format == "ssmd"


def test_markdown_inputs_are_canonicalized_consistently(tmp_path: Path):
    for suffix in (".md", ".markdown", ".mdown", ".mkd", ".MD"):
        source = tmp_path / f"episode{suffix}"
        source.write_text("# heading", encoding="utf-8")
        automatic = document_from_file(source)
        explicit = document_from_file(source, input_format="markdown")
        in_memory = document_from_text(
            "# heading", input_format="markdown", source_name=source.name
        )

        assert automatic.format == explicit.format == in_memory.format == "ssmd"
        assert automatic.text == explicit.text == in_memory.text
        assert automatic.provenance is not None
        assert automatic.provenance.source_format == "markdown"

    assert document_from_text("# heading").format == "text"
    assert document_from_stdin("# heading", input_format="markdown").format == "ssmd"


def test_rich_markdown_paths_produce_identical_canonical_ssmd(tmp_path: Path):
    fixture = Path(__file__).parent / "fixtures" / "markdown" / "all-elements.md"
    content = fixture.read_text(encoding="utf-8")
    source = tmp_path / fixture.name
    source.write_text(content, encoding="utf-8")

    automatic = document_from_file(source)
    explicit = document_from_file(source, input_format="markdown")
    in_memory = document_from_text(content, input_format="markdown", source_name=source.name)

    assert automatic.text == explicit.text == in_memory.text


def test_explicit_file_formats_preserve_textual_interpretation(tmp_path: Path):
    source = tmp_path / "episode.md"
    source.write_text("# heading", encoding="utf-8")

    assert document_from_file(source, input_format="text").format == "text"
    assert document_from_file(source, input_format="text").text == "# heading"
    assert document_from_file(source, input_format="ssmd").format == "ssmd"
    assert document_from_file(source, input_format="ssmd").text == "# heading"
