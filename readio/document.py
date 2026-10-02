from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from .integrations.ssmdconvert import (
    CanonicalDocument,
    convert_document_content,
    convert_document_source,
)
from .jsonutil import JsonValue

InputFormat = Literal["text", "markdown", "ssmd"]
InputFormatRequest = Literal["auto", "text", "markdown", "ssmd"]
MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".mdown", ".mkd"})


@dataclass(frozen=True, slots=True)
class DocumentProvenance:
    source_format: str | None = None
    media_type: str | None = None
    source_name: str | None = None
    converter: str | None = None
    converter_version: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class InputDocument:
    text: str
    source_path: Path | None
    format: InputFormat
    provenance: DocumentProvenance | None = None
    canonical_sha256: str | None = None


def infer_input_format(path: Path | None) -> InputFormat:
    if path is None:
        return "text"
    name = path.name.lower()
    if name.endswith((".ssmd.md", ".ssmd")):
        return "ssmd"
    suffix = path.suffix.lower()
    if suffix in MARKDOWN_SUFFIXES:
        return "markdown"
    return "text"


def resolve_input_format(
    requested: InputFormatRequest,
    *,
    source_path: Path | None,
) -> InputFormat:
    return infer_input_format(source_path) if requested == "auto" else requested


def _document_from_canonical(
    converted: CanonicalDocument,
) -> InputDocument:
    return InputDocument(
        text=converted.ssmd,
        source_path=converted.source,
        format="ssmd",
        provenance=DocumentProvenance(
            source_format=converted.source_format,
            media_type=converted.media_type,
            source_name=converted.source_name,
            converter="ssmdconvert",
            converter_version=converted.converter_version,
            metadata=converted.metadata,
        ),
        canonical_sha256=hashlib.sha256(converted.ssmd.encode("utf-8")).hexdigest(),
    )


def canonicalize_document(
    document: InputDocument,
    *,
    source_name: str | None = None,
) -> InputDocument:
    if document.format != "markdown":
        return document
    logical_name = (
        source_name
        or (document.provenance.source_name if document.provenance else None)
        or (document.source_path.name if document.source_path else "<memory>")
    )
    converted = convert_document_content(
        document.text,
        input_format="markdown",
        source_name=logical_name,
        source_path=document.source_path,
    )
    return _document_from_canonical(converted)


def document_from_text(
    text: str,
    *,
    source_path: Path | None = None,
    input_format: InputFormat = "text",
    source_name: str | None = None,
) -> InputDocument:
    document = InputDocument(text=text, source_path=source_path, format=input_format)
    return canonicalize_document(document, source_name=source_name)


def document_from_file(
    path: Path,
    *,
    input_format: InputFormatRequest = "auto",
) -> InputDocument:
    source = path.expanduser()
    resolved_format = infer_input_format(source) if input_format == "auto" else input_format
    if resolved_format == "ssmd" and source.is_file():
        raw = source.read_bytes()
        return InputDocument(
            text=raw.decode("utf-8-sig"),
            source_path=source.resolve(),
            format="ssmd",
            provenance=DocumentProvenance(
                source_format="ssmd",
                media_type="text/markdown",
                source_name=source.name,
            ),
            canonical_sha256=hashlib.sha256(raw).hexdigest(),
        )
    if input_format == "auto":
        return _document_from_canonical(convert_document_source(source))
    if resolved_format == "markdown":
        return document_from_text(
            source.read_text(encoding="utf-8"),
            source_path=source,
            input_format="markdown",
            source_name=source.name,
        )
    return InputDocument(
        text=source.read_text(encoding="utf-8"),
        source_path=source,
        format=resolved_format,
    )


def document_from_stdin(
    text: str,
    *,
    input_format: InputFormat = "text",
) -> InputDocument:
    return document_from_text(text, input_format=input_format)
