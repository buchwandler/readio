from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

InputFormat = Literal["text", "markdown", "ssmd"]
InputFormatRequest = Literal["auto", "text", "markdown", "ssmd"]
MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".mdown", ".mkd"})


@dataclass(frozen=True, slots=True)
class InputDocument:
    text: str
    source_path: Path | None
    format: InputFormat


def infer_input_format(path: Path | None) -> InputFormat:
    if path is None:
        return "text"
    suffix = path.suffix.lower()
    if suffix == ".ssmd":
        return "ssmd"
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
    return InputDocument(
        text=source.read_text(encoding="utf-8"),
        source_path=source,
        format=resolve_input_format(input_format, source_path=source),
    )


def document_from_stdin(
    text: str,
    *,
    input_format: InputFormat = "text",
) -> InputDocument:
    return document_from_text(text, input_format=input_format)
