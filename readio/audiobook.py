"""Book-source inspection and chapter-scoped audiobook project creation."""

from __future__ import annotations

import os
import secrets
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from ssmdconvert import (
    Book,
    BookChapter,
    BookInspectionChapter,
    ChapterSelectionError,
    UnsupportedBookSourceError,
    convert_book,
    inspect_book,
    load_book_bundle,
    write_book_bundle,
)
from ssmdconvert import __version__ as ssmdconvert_version

from .chapter_selection import parse_chapter_selection
from .project import (
    Project,
    atomic_write_json,
    canonical_json,
    hash_file,
    load_project,
    sha256_bytes,
)
from .project_model import DocumentIndex, DocumentScope, ProjectManifest


@dataclass(frozen=True, slots=True)
class AudiobookChapter:
    number: int
    source_id: str | None
    title: str
    href: str | None
    source_parent_id: str | None
    parent_id: str | None
    level: int
    char_count: int | None
    diagnostics: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class AudiobookInspection:
    source: Path
    metadata: Mapping[str, Any]
    chapters: tuple[AudiobookChapter, ...]


def _book_source_kind(source: Path) -> Literal["epub", "bundle"]:
    name = source.name.casefold()
    if source.is_dir() and name.endswith(".ssmdbook"):
        return "bundle"
    if source.is_file():
        if name.endswith(".ssmdbook.zip"):
            return "bundle"
        if source.suffix.casefold() == ".epub":
            return "epub"
    if not source.exists():
        raise FileNotFoundError(source)
    raise UnsupportedBookSourceError(
        f"unsupported book source {source.name!r}; expected an EPUB or .ssmdbook bundle"
    )


def _inspection_chapter(chapter: BookInspectionChapter | BookChapter) -> AudiobookChapter:
    return AudiobookChapter(
        number=chapter.source_number,
        source_id=chapter.source_id,
        title=chapter.title,
        href=chapter.href,
        source_parent_id=chapter.source_parent_id,
        parent_id=chapter.parent_id,
        level=chapter.level,
        char_count=chapter.char_count,
        diagnostics=tuple(dict(item) for item in chapter.diagnostics),
    )


def inspect_book_source(source: Path) -> AudiobookInspection:
    """Inspect an EPUB or a validated ssmdconvert book bundle."""
    source_path = source.expanduser().resolve()
    source_kind = _book_source_kind(source_path)
    if source_kind == "epub":
        inspection = inspect_book(source_path)
        metadata = inspection.metadata
        raw_chapters = inspection.chapters
    else:
        book = load_book_bundle(source_path)
        metadata = book.metadata
        raw_chapters = book.chapters
    return AudiobookInspection(
        source=source_path,
        metadata=dict(metadata),
        chapters=tuple(_inspection_chapter(chapter) for chapter in raw_chapters),
    )


def _select_bundle_chapters(book: Book, chapters: str | None) -> Book:
    try:
        selected_numbers = set(
            parse_chapter_selection(
                chapters,
                available_numbers=tuple(chapter.source_number for chapter in book.chapters),
            )
        )
    except ValueError as error:
        raise ChapterSelectionError(str(error)) from error
    return replace(
        book,
        chapters=tuple(
            chapter for chapter in book.chapters if chapter.source_number in selected_numbers
        ),
    )


def _source_snapshot_name(source: Path, source_kind: Literal["epub", "bundle"]) -> str:
    if source_kind == "bundle" and source.is_dir():
        return f"{source.name}.zip"
    return source.name


def _project_basename(source: Path, source_kind: Literal["epub", "bundle"]) -> str:
    name = source.name
    if source_kind == "bundle" and name.casefold().endswith(".ssmdbook.zip"):
        return name[: -len(".ssmdbook.zip")]
    if source_kind == "bundle" and source.is_dir() and name.casefold().endswith(".ssmdbook"):
        return name[: -len(".ssmdbook")]
    return source.stem


def init_audiobook_project(
    source: Path,
    output: Path | None = None,
    chapters: str | None = "all",
) -> Project:
    """Create an atomic audiobook project from an EPUB or ssmdconvert bundle."""
    source_path = source.expanduser().resolve()
    source_kind = _book_source_kind(source_path)
    root = Path(output or f"{_project_basename(source_path, source_kind)}.readio").expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    root = root.resolve()
    if root.exists():
        raise ValueError(f"project destination already exists: {root}")
    if source_kind == "epub":
        book = convert_book(source_path, chapters=chapters)
        source_book = None
        source_format = book.source.format
    else:
        source_book = load_book_bundle(source_path)
        book = _select_bundle_chapters(source_book, chapters)
        source_format = "ssmdbook"

    source_relative = (Path("source") / _source_snapshot_name(source_path, source_kind)).as_posix()
    temporary = root.with_name(f".{root.name}.tmp-{secrets.token_hex(8)}")
    try:
        for directory in (
            "source",
            "document/chapters",
            "plan/chapters",
            "synthesis/segments",
            "synthesis/cache",
            "composition/parts",
            "output",
            "report",
        ):
            (temporary / directory).mkdir(parents=True, exist_ok=True)

        snapshot_path = temporary / source_relative
        if source_book is not None and source_path.is_dir():
            write_book_bundle(source_book, snapshot_path, format="zip")
        else:
            shutil.copyfile(source_path, snapshot_path)
        source_sha256 = hash_file(snapshot_path)
        project_identity = {
            "name": root.stem,
            "kind": "audiobook",
            "source": {
                "path": source_relative,
                "format": source_format,
                "sha256": source_sha256,
            },
            "chapters": [chapter.source_number for chapter in book.chapters],
        }
        manifest = ProjectManifest(
            project_id=f"sha256:{sha256_bytes(canonical_json(project_identity))}",
            name=root.stem,
            source_path=source_relative,
            source_format=source_format,
            source_sha256=source_sha256,
            kind="audiobook",
            schema_version=3,
        )

        scopes = []
        for chapter in book.chapters:
            relative_path = Path("document") / "chapters" / f"{chapter.id}.ssmd.md"
            chapter_path = temporary / relative_path
            chapter_path.write_text(chapter.ssmd, encoding="utf-8")
            scopes.append(
                DocumentScope(
                    id=chapter.id,
                    kind="chapter",
                    path=relative_path.as_posix(),
                    input_format="ssmd",
                    title=chapter.title,
                    source_number=chapter.source_number,
                    source_id=chapter.source_id,
                    href=chapter.href,
                    parent_id=chapter.parent_id,
                    level=chapter.level,
                    char_count=chapter.char_count,
                    extracted_sha256=hash_file(chapter_path),
                    diagnostics=chapter.diagnostics,
                    source_parent_id=chapter.source_parent_id,
                )
            )
        metadata = dict(book.metadata)
        metadata["conversion"] = {
            "tool": "ssmdconvert",
            "version": ssmdconvert_version,
            "source_format": source_format,
            "source_name": book.source.name or source_path.name,
            "media_type": book.source.media_type,
        }
        document_index = DocumentIndex(
            scopes=tuple(scopes),
            metadata=metadata,
            selection=tuple(chapter.source_number for chapter in book.chapters),
        )
        atomic_write_json(temporary / manifest.document_index_path, document_index.to_dict())
        atomic_write_json(temporary / "project.json", manifest.to_dict())
        os.replace(temporary, root)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return load_project(root)


__all__ = [
    "AudiobookChapter",
    "AudiobookInspection",
    "init_audiobook_project",
    "inspect_book_source",
]
