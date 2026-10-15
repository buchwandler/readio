"""Legacy attached-workspace index compatibility and book-source inspection.

New audiobook workflows use :mod:`readio.book_pipeline` and never create Readio-owned
project state. ``refresh_audiobook_index`` exists only for already-attached schema-v4
projects opened through the generic project compatibility API.
"""

from __future__ import annotations


from pathlib import Path
from typing import Any
from .integrations.ssmdconvert import (
    BookInspection as AudiobookInspection,
    BookInspectionChapter as AudiobookChapter,
    CanonicalBook,
    inspect_book_source,
    load_book_workspace_source,
)
from .project import Project, ProjectError, atomic_write_json, hash_file, project_lock
from .project_model import DocumentIndex, DocumentScope


def init_audiobook_project(
    source: Path,
    output: Path | None = None,
    chapters: str | None = "all",
    *,
    language: str | None = None,
) -> Any:
    """Compatibility alias returning a native workspace instead of a Readio project."""

    from . import book_pipeline

    source_path = Path(source).expanduser().resolve()
    if source_path.is_dir() and source_path.name.casefold().endswith(".ssmdbook"):
        if output is not None or language is not None:
            raise ValueError("output and language are only supported when converting a source")
        return book_pipeline.open_book_workspace(source_path)
    return book_pipeline.convert_book_source(
        source_path, output, chapters=chapters or "all", language=language
    )


def refresh_audiobook_index(project: Project) -> CanonicalBook:
    """Refresh the derived index of an already-attached legacy audiobook project."""
    if project.manifest.schema_version != 4 or project.manifest.layout_mode != "attached-ssmdbook":
        raise ProjectError("audiobook index refresh requires an attached schema-v4 project")
    with project_lock(project, operation="workspace-index-refresh"):
        book = load_book_workspace_source(project.workspace_root)
        index_path = project.paths["document_index"]
        current_index = project.load_document_index() if index_path.is_file() else None
        if current_index is None:
            selected_ids = tuple(chapter.id for chapter in book.chapters)
        else:
            previous_numbers = tuple(scope.source_number for scope in current_index.scopes)
            if (
                any(number is None for number in previous_numbers)
                or tuple(current_index.selection) != previous_numbers
            ):
                raise ProjectError("attached audiobook chapter selection/index is inconsistent")
            selected_ids = tuple(scope.id for scope in current_index.scopes)
            available_ids = {chapter.id for chapter in book.chapters}
            missing_ids = tuple(
                scope_id for scope_id in selected_ids if scope_id not in available_ids
            )
            if missing_ids:
                missing_text = ", ".join(missing_ids)
                raise ProjectError(
                    f"selected audiobook chapter ID(s) {missing_text} no longer exist in the workspace"
                )
        selected_id_set = set(selected_ids)
        selected = tuple(chapter for chapter in book.chapters if chapter.id in selected_id_set)
        scopes = []
        for chapter in selected:
            relative_path = book.chapter_paths.get(chapter.id)
            if relative_path is None:
                raise ProjectError(f"workspace index has no path for chapter {chapter.id!r}")
            chapter_path = project.workspace_path(relative_path)
            scopes.append(
                DocumentScope(
                    id=chapter.id,
                    kind="chapter",
                    path=relative_path,
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
        refreshed = DocumentIndex(
            scopes=tuple(scopes),
            metadata=dict(book.metadata),
            selection=tuple(chapter.source_number for chapter in selected),
        )
        if current_index != refreshed:
            atomic_write_json(index_path, refreshed.to_dict())
    return book


__all__ = [
    "AudiobookChapter",
    "init_audiobook_project",
    "AudiobookInspection",
    "inspect_book_source",
    "refresh_audiobook_index",
]
