from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

InputFormat = Literal["text", "ssmd"]


@dataclass(frozen=True, slots=True)
class InputDocument:
    text: str
    source_path: Path | None
    format: InputFormat


def document_from_text(text: str, *, source_path: Path | None = None) -> InputDocument:
    return InputDocument(text=text, source_path=source_path, format="text")


def document_from_file(path: Path) -> InputDocument:
    source = path.expanduser()
    return InputDocument(
        text=source.read_text(encoding="utf-8"),
        source_path=source,
        format="ssmd" if source.suffix.lower() == ".ssmd" else "text",
    )


def document_from_stdin(text: str) -> InputDocument:
    return document_from_text(text)
