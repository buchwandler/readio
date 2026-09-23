"""EPUB inspection and initialization for chapter-scoped Readio projects."""

from __future__ import annotations

import os
import secrets
import shutil
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from epub2text import ChapterMarkdownOptions, EPUBParser

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
    source_id: str
    title: str
    href: str | None
    parent_id: str | None
    level: int
    char_count: int
    markdown: str
    diagnostics: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class AudiobookInspection:
    source: Path
    metadata: Mapping[str, Any]
    chapters: tuple[AudiobookChapter, ...]


def _epub_metadata(value: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "title": getattr(value, "title", None),
        "authors": list(getattr(value, "authors", ()) or ()),
        "language": getattr(value, "language", None),
    }
    for key in ("publisher", "identifier"):
        item = getattr(value, key, None)
        if item:
            metadata[key] = item
    return metadata


def inspect_epub(source: Path) -> AudiobookInspection:
    """Inspect an EPUB through epub2text's public chapter-document API."""
    source_path = source.expanduser().resolve()
    if not source_path.is_file():
        raise ValueError(f"source is not a regular file: {source_path}")
    if source_path.suffix.lower() != ".epub":
        raise ValueError(f"source is not an EPUB file: {source_path.name}")
    try:
        parser = EPUBParser(str(source_path))
        metadata = _epub_metadata(parser.get_metadata())
        documents = parser.get_chapter_documents(
            options=ChapterMarkdownOptions(
                include_title=False,
                minimum_body_heading_level=2,
                preserve_emphasis=True,
                preserve_strong=True,
                link_mode="preserve",
                code_mode="preserve",
                resolve_css_emphasis=True,
                preserve_scene_breaks=True,
            )
        )
    except Exception as exc:
        raise ValueError(f"could not read EPUB {source_path.name}: {exc}") from exc
    if not documents:
        raise ValueError(f"no chapters were discovered in {source_path.name}")
    chapters = tuple(
        AudiobookChapter(
            number=index,
            source_id=str(document.id),
            title=str(document.title or f"Chapter {index}"),
            href=document.href,
            parent_id=document.parent_id,
            level=int(document.level),
            char_count=int(document.char_count),
            markdown=document.to_markdown(include_title=True, title_level=1),
            diagnostics=tuple(asdict(item) for item in document.diagnostics),
        )
        for index, document in enumerate(documents, 1)
    )
    return AudiobookInspection(source_path, metadata, chapters)


def init_audiobook_project(
    source: Path,
    output: Path | None = None,
    chapters: str | None = "all",
) -> Project:
    """Create an atomic persistent project containing selected EPUB chapters."""
    source_path = source.expanduser().resolve()
    if not source_path.is_file():
        raise ValueError(f"source is not a regular file: {source_path}")
    if source_path.suffix.lower() != ".epub":
        raise ValueError(f"source is not an EPUB file: {source_path.name}")
    root = Path(output or f"{source_path.stem}.readio").expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    root = root.resolve()
    if root.exists():
        raise ValueError(f"project destination already exists: {root}")

    inspection = inspect_epub(source_path)
    selected = parse_chapter_selection(chapters, len(inspection.chapters))
    source_relative = (Path("source") / source_path.name).as_posix()
    source_sha256 = hash_file(source_path)
    project_identity = {
        "name": root.stem,
        "kind": "audiobook",
        "source": {
            "path": source_relative,
            "format": "epub",
            "sha256": source_sha256,
        },
    }
    manifest = ProjectManifest(
        project_id=f"sha256:{sha256_bytes(canonical_json(project_identity))}",
        name=root.stem,
        source_path=source_relative,
        source_format="epub",
        source_sha256=source_sha256,
        kind="audiobook",
        schema_version=2,
    )
    temporary = root.with_name(f".{root.name}.tmp-{secrets.token_hex(8)}")
    scopes = []
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
        shutil.copyfile(source_path, temporary / source_relative)
        for chapter_index in selected:
            chapter = inspection.chapters[chapter_index]
            scope_id = f"chapter-{chapter.number:04d}"
            relative_path = Path("document") / "chapters" / f"{scope_id}.md"
            chapter_path = temporary / relative_path
            chapter_path.write_text(chapter.markdown, encoding="utf-8")
            scopes.append(
                DocumentScope(
                    id=scope_id,
                    kind="chapter",
                    path=relative_path.as_posix(),
                    input_format="markdown",
                    title=chapter.title,
                    source_number=chapter.number,
                    source_id=chapter.source_id,
                    href=chapter.href,
                    parent_id=chapter.parent_id,
                    level=chapter.level,
                    char_count=chapter.char_count,
                    extracted_sha256=hash_file(chapter_path),
                    diagnostics=chapter.diagnostics,
                )
            )
        document_index = DocumentIndex(
            scopes=tuple(scopes),
            metadata=dict(inspection.metadata),
            selection=tuple(inspection.chapters[index].number for index in selected),
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
    "inspect_epub",
]
