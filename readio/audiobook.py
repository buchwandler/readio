"""Book-source inspection and chapter-scoped audiobook project creation."""

from __future__ import annotations

import os
import secrets
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Literal

from .chapter_selection import parse_chapter_selection
from .integrations.ssmdconvert import (
    BookInspection,
    BookInspectionChapter,
    BookSelectionError,
    CanonicalBook,
    book_source_kind,
    convert_book_source,
    inspect_book_source,
    load_book_bundle_source,
    write_book_bundle,
)
from .project import (
    Project,
    atomic_write_json,
    canonical_json,
    hash_file,
    load_project,
    sha256_bytes,
)
from .project_model import DocumentIndex, DocumentScope, ProjectManifest

AudiobookChapter = BookInspectionChapter
AudiobookInspection = BookInspection


def _select_bundle_chapters(
    book: CanonicalBook,
    chapters: str | None,
    source_path: Path,
) -> CanonicalBook:
    try:
        selected_numbers = set(
            parse_chapter_selection(
                chapters,
                available_numbers=tuple(chapter.source_number for chapter in book.chapters),
            )
        )
    except ValueError as error:
        raise BookSelectionError(str(error), source_path=source_path) from error
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
    source_kind = book_source_kind(source_path)
    root = Path(output or f"{_project_basename(source_path, source_kind)}.readio").expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    root = root.resolve()
    if root.exists():
        raise ValueError(f"project destination already exists: {root}")
    if source_kind == "epub":
        book = convert_book_source(source_path, chapters=chapters)
        source_book = None
        source_format = book.source_format
    else:
        source_book = load_book_bundle_source(source_path)
        book = _select_bundle_chapters(source_book, chapters, source_path)
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
            "version": book.converter_version,
            "source_format": source_format,
            "source_name": book.source_name or source_path.name,
            "media_type": book.media_type,
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
