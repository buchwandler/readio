from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from .conversion import convert_document_source
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


def document_from_text(
    text: str,
    *,
    source_path: Path | None = None,
    input_format: InputFormat = "text",
) -> InputDocument:
    return InputDocument(text=text, source_path=source_path, format=input_format)


def document_from_file(
    path: Path,
    *,
    input_format: InputFormatRequest = "auto",
) -> InputDocument:
    source = path.expanduser()
    if input_format == "auto":
        converted = convert_document_source(source)
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
        )
    return InputDocument(
        text=source.read_text(encoding="utf-8"),
        source_path=source,
        format=input_format,
    )


def document_from_stdin(
    text: str,
    *,
    input_format: InputFormat = "text",
) -> InputDocument:
    return document_from_text(text, input_format=input_format)
