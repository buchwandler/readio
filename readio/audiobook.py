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
    load_book_workspace_source,
    write_book_bundle,
)
from .project import (
    Project,
    ProjectError,
    atomic_write_json,
    canonical_json,
    hash_file,
    load_project,
    project_lock,
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


def _project_basename(source: Path, source_kind: Literal["epub", "bundle"]) -> str:
    name = source.name
    if source_kind == "bundle" and name.casefold().endswith(".ssmdbook.zip"):
        return name[: -len(".ssmdbook.zip")]
    if source_kind == "bundle" and source.is_dir() and name.casefold().endswith(".ssmdbook"):
        return name[: -len(".ssmdbook")]
    return source.stem


def refresh_audiobook_index(project: Project) -> CanonicalBook:
    """Refresh only Readio's attached-book index from canonical workspace bytes."""
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


def _attach_existing_workspace(
    workspace: Path, chapters: str | None, *, identity_root: Path | None = None
) -> Project:
    workspace = workspace.expanduser().resolve()
    identity_root = (identity_root or workspace).expanduser().resolve()
    state_root = workspace / ".readio"
    if state_root.exists() or state_root.is_symlink():
        raise ProjectError(f"project already exists for workspace: {workspace}")

    book = load_book_workspace_source(workspace)
    selected_book = _select_bundle_chapters(book, chapters, workspace)
    manifest_sha256 = hash_file(workspace / "manifest.json")
    name = _project_basename(identity_root, "bundle")
    identity = {
        "kind": "audiobook",
        "layout": "attached-ssmdbook",
        "workspace": identity_root.as_posix(),
        "manifest_sha256": manifest_sha256,
    }
    manifest = ProjectManifest(
        project_id=f"sha256:{sha256_bytes(canonical_json(identity))}",
        name=name,
        source_path="manifest.json",
        source_format="ssmdbook",
        source_sha256=manifest_sha256,
        kind="audiobook",
        schema_version=4,
        layout_mode="attached-ssmdbook",
        workspace_manifest_path="manifest.json",
        workspace_manifest_sha256=manifest_sha256,
    )
    temporary = workspace / f".readio.tmp-{secrets.token_hex(8)}"
    try:
        temporary.mkdir()
        for directory in (
            "document",
            "plan",
            "synthesis/segments",
            "synthesis/cache",
            "composition/parts",
            "output",
            "report",
        ):
            (temporary / directory).mkdir(parents=True, exist_ok=True)

        scopes = []
        for chapter in selected_book.chapters:
            relative_path = selected_book.chapter_paths.get(chapter.id)
            if relative_path is None:
                raise ProjectError(f"workspace index has no path for chapter {chapter.id!r}")
            chapter_relative = Path(relative_path)
            if chapter_relative.is_absolute() or ".." in chapter_relative.parts:
                raise ProjectError(f"workspace chapter path is not relative: {relative_path!r}")
            chapter_path = (workspace / chapter_relative).resolve()
            try:
                chapter_path.relative_to(workspace)
            except ValueError as error:
                raise ProjectError(
                    f"workspace chapter path escapes the book: {relative_path!r}"
                ) from error
            if not chapter_path.is_file():
                raise ProjectError(f"workspace chapter is missing: {relative_path!r}")
            scopes.append(
                DocumentScope(
                    id=chapter.id,
                    kind="chapter",
                    path=chapter_relative.as_posix(),
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

        metadata = dict(selected_book.metadata)
        metadata["conversion"] = {
            "tool": "ssmdconvert",
            "version": selected_book.converter_version,
            "source_format": "ssmdbook",
            "source_name": identity_root.name,
            "media_type": selected_book.media_type,
        }
        index = DocumentIndex(
            scopes=tuple(scopes),
            metadata=metadata,
            selection=tuple(chapter.source_number for chapter in selected_book.chapters),
        )
        atomic_write_json(temporary / manifest.document_index_path, index.to_dict())
        atomic_write_json(temporary / "project.json", manifest.to_dict())
        os.replace(temporary, state_root)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return load_project(workspace)


def _materialize_book_workspace(
    source: Path,
    output: Path | None,
    chapters: str | None,
    *,
    source_kind: Literal["epub", "bundle"],
    language: str | None,
) -> Project:

    requested_output = output or Path(f"{_project_basename(source, source_kind)}.ssmdbook")
    root = requested_output.expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    root = root.parent.resolve() / root.name
    if not root.name.casefold().endswith(".ssmdbook"):
        raise ValueError("workspace destination must have a .ssmdbook suffix")
    if root.exists() or root.is_symlink():
        raise ValueError(f"workspace destination already exists: {root}")
    if source_kind == "epub":
        book = convert_book_source(source, chapters="all", language=language)
    else:
        if language is not None:
            raise ValueError("--language is only supported for EPUB conversion")
        book = load_book_bundle_source(source)
    _select_bundle_chapters(book, chapters, source)

    temporary = root.with_name(f".{root.stem}.tmp-{secrets.token_hex(8)}.ssmdbook")
    try:
        write_book_bundle(book, temporary, format="directory")
        _attach_existing_workspace(temporary, chapters, identity_root=root)
        os.rename(temporary, root)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return load_project(root)


def init_audiobook_project(
    source: Path,
    output: Path | None = None,
    chapters: str | None = "all",
    *,
    language: str | None = None,
) -> Project:
    """Attach a workspace or materialize EPUB/ZIP input as an editable book workspace."""
    source_path = source.expanduser().resolve()
    source_kind = book_source_kind(source_path)
    if source_kind == "bundle" and source_path.is_dir():
        if language is not None:
            raise ValueError("--language is only supported for EPUB conversion")
        if output is not None:
            raise ValueError(
                "--output is not supported when attaching an existing .ssmdbook workspace"
            )
        return _attach_existing_workspace(source_path, chapters)
    return _materialize_book_workspace(
        source_path,
        output,
        chapters,
        source_kind=source_kind,
        language=language,
    )


__all__ = [
    "AudiobookChapter",
    "AudiobookInspection",
    "init_audiobook_project",
    "inspect_book_source",
    "refresh_audiobook_index",
]
