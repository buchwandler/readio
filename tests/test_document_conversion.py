from __future__ import annotations

import hashlib
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import pytest
from document_support import write_test_docx, write_test_pdf
from ssmdconvert import (
    MissingDependencyError,
    SSMDConvertError,
    UnsupportedInputError,
)

from readio.api import Document, DocumentProvenance
from readio.document import document_from_file
from readio.errors import InputError
from readio.integrations import ssmdconvert as conversion
from readio.integrations.ssmdconvert import CanonicalDocument, MissingInputDependencyError

_SSMD = "---\nssmd_version: '0.9'\n---\nHello.\n"


def _write_epub(path: Path) -> None:
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        archive.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
            'unique-identifier="book-id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="book-id">urn:readio:test</dc:identifier>'
            "<dc:title>Test Book</dc:title></metadata>"
            '<manifest><item id="chapter" href="chapter.xhtml" '
            'media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="chapter"/></spine></package>',
        )
        archive.writestr(
            "OEBPS/chapter.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter</title></head>'
            "<body><h1>Chapter</h1><p>EPUB text</p></body></html>",
        )


@pytest.mark.parametrize(
    ("suffix", "content"),
    [
        (".txt", "plain text"),
        (".md", "# Markdown heading"),
        (".html", "<html><body><p>HTML text</p></body></html>"),
        (".ssmd", _SSMD),
        (".ssmd.md", _SSMD),
    ],
)
def test_convert_document_source_returns_canonical_ssmd(
    tmp_path: Path, suffix: str, content: str
) -> None:
    source = tmp_path / f"source{suffix}"
    source.write_text(content, encoding="utf-8")

    converted = conversion.convert_document_source(source)

    assert isinstance(converted, CanonicalDocument)
    assert converted.source == source.resolve()
    assert converted.source_format
    assert converted.source_name == source.name
    assert converted.ssmd
    assert "ssmd_version:" in converted.ssmd
    assert converted.converter_version
    assert isinstance(converted.metadata, dict)


def test_convert_document_source_supports_pdf(tmp_path: Path) -> None:
    pytest.importorskip("pypdf")
    source = tmp_path / "report.pdf"
    write_test_pdf(source, "PDF text")

    converted = conversion.convert_document_source(source)

    assert converted.source_format == "pdf"
    assert converted.media_type == "application/pdf"
    assert "PDF text" in converted.ssmd


def test_convert_document_source_supports_docx(tmp_path: Path) -> None:
    pytest.importorskip("docx")
    source = tmp_path / "report.docx"
    write_test_docx(source)

    converted = conversion.convert_document_source(source)

    assert converted.source_format == "docx"
    assert "DOCX text" in converted.ssmd


def test_convert_document_source_supports_epub(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    _write_epub(source)

    converted = conversion.convert_document_source(source)

    assert converted.source_format == "epub"
    assert "EPUB text" in converted.ssmd


def test_document_from_file_auto_uses_conversion_and_explicit_text_does_not(
    tmp_path: Path,
) -> None:
    source = tmp_path / "notes.md"
    source.write_text("# Markdown heading", encoding="utf-8")

    converted = document_from_file(source)
    literal = document_from_file(source, input_format="text")

    assert converted.format == "ssmd"
    assert "ssmd_version:" in converted.text
    assert isinstance(converted, Document)
    assert isinstance(converted.provenance, DocumentProvenance)
    expected = conversion.convert_document_source(source)
    assert converted.provenance is not None
    assert converted.provenance.source_format == expected.source_format
    assert converted.provenance.media_type == expected.media_type
    assert converted.provenance.source_name == expected.source_name
    assert converted.provenance.converter == "ssmdconvert"
    assert converted.provenance.converter_version == expected.converter_version
    assert dict(converted.provenance.metadata) == dict(expected.metadata)
    assert converted.source_path == source.resolve()
    assert literal.format == "text"
    assert literal.provenance is None
    assert literal.text == "# Markdown heading"


def test_canonical_ssmd_file_bypasses_conversion_and_keeps_source_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    source = tmp_path / "episode.ssmd.md"
    raw = b"\xef\xbb\xbf" + _SSMD.encode("utf-8")
    source.write_bytes(raw)
    monkeypatch.setattr(
        "readio.document.convert_document_source",
        lambda _path: pytest.fail("canonical SSMD must not be converted again"),
    )

    document = document_from_file(source)

    assert document.text == _SSMD
    assert document.canonical_sha256 == hashlib.sha256(raw).hexdigest()
    assert document.provenance is not None
    assert document.provenance.converter is None


def test_raw_auto_file_calls_ssmdconvert_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("Raw source", encoding="utf-8")
    original_convert = conversion.convert_document_source
    calls = 0

    def count_conversion(path: Path):
        nonlocal calls
        calls += 1
        return original_convert(path)

    monkeypatch.setattr("readio.document.convert_document_source", count_conversion)

    document = document_from_file(source)

    assert calls == 1
    assert document.canonical_sha256 == hashlib.sha256(document.text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (UnsupportedInputError("unsupported"), "input.format_unsupported"),
        (MissingDependencyError("missing adapter"), "input.converter_dependency_missing"),
        (SSMDConvertError("conversion failed"), "input.conversion_failed"),
        (OSError("read failed"), "input.conversion_failed"),
    ],
)
def test_convert_document_source_translates_converter_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    error: Exception,
    code: str,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("text", encoding="utf-8")

    def fail(_path: Path):
        raise error

    monkeypatch.setattr(conversion, "ssmdconvert_convert", fail)

    with pytest.raises(InputError) as caught:
        conversion.convert_document_source(source)

    assert caught.value.code == code
    if code == "input.converter_dependency_missing":
        assert isinstance(caught.value, MissingInputDependencyError)
    assert caught.value.source_path == source.resolve()


def test_convert_document_source_reports_unsupported_suffix(tmp_path: Path) -> None:
    source = tmp_path / "source.unsupported"
    source.write_text("not a supported format", encoding="utf-8")

    with pytest.raises(InputError) as caught:
        conversion.convert_document_source(source)

    assert caught.value.code == "input.format_unsupported"
    assert caught.value.source_path == source.resolve()


def test_convert_document_source_reports_missing_source(tmp_path: Path) -> None:
    source = tmp_path / "missing.txt"

    with pytest.raises(InputError) as caught:
        conversion.convert_document_source(source)

    assert caught.value.code == "input.not_found"
    assert caught.value.source_path == source.resolve()
