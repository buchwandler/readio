"""Native SSMDConvert workspace resolution and creation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import ssmdconvert  # type: ignore[import-not-found]

WorkspaceKind = Literal["document", "book"]
WorkspaceInputKind = Literal["auto", "document", "book"]


@dataclass(frozen=True, slots=True)
class WorkspaceRef:
    """Small immutable dispatch reference for an SSMDConvert workspace."""

    root: Path
    kind: WorkspaceKind
    document_id: str | None = None
    chapter_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CreateWorkspaceRequest:
    """Options for converting a source into a native SSMDConvert workspace."""

    source: Path
    output: Path | None = None
    kind: WorkspaceInputKind = "auto"
    language: str | None = None
    chapters: str = "all"
    force: bool = False


def resolve_workspace(path: str | Path) -> WorkspaceRef:
    """Load a valid native workspace by its strict `.ssmdoc`/`.ssmdbook` suffix."""

    root = Path(path).expanduser().resolve()
    name = root.name.casefold()
    if not name.endswith((".ssmdoc", ".ssmdbook")):
        raise ValueError("workspace path must end in .ssmdoc or .ssmdbook")
    if not root.exists():
        raise FileNotFoundError(root)

    if name.endswith(".ssmdoc"):
        document_native = ssmdconvert.load_document_workspace(root)
        return WorkspaceRef(
            root=root,
            kind="document",
            document_id=document_native.status.id,
        )
    book_native = ssmdconvert.load_book_workspace(root)
    return WorkspaceRef(
        root=root,
        kind="book",
        chapter_ids=tuple(chapter.id for chapter in book_native.book.chapters),
    )


def create_workspace(
    source: str | Path,
    request: CreateWorkspaceRequest | None = None,
    *,
    on_progress: Callable[[Any], None] | None = None,
) -> WorkspaceRef:
    """Convert a source into a native workspace, or resolve an existing one."""

    source_path = Path(source).expanduser().resolve()
    options = request or CreateWorkspaceRequest(source=source_path)
    if Path(options.source).expanduser().resolve() != source_path:
        raise ValueError("request.source must match the source argument")
    if options.kind not in {"auto", "document", "book"}:
        raise ValueError(f"unsupported workspace kind: {options.kind}")
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    source_name = source_path.name.casefold()
    if source_name.endswith((".ssmdoc", ".ssmdbook")):
        workspace = resolve_workspace(source_path)
        if options.kind != "auto" and options.kind != workspace.kind:
            raise ValueError(
                f"requested {options.kind} workspace, but input is a {workspace.kind} workspace"
            )
        return workspace

    kind: WorkspaceKind
    if options.kind == "auto":
        kind = "book" if source_path.suffix.casefold() == ".epub" else "document"
    else:
        kind = options.kind

    destination = _workspace_destination(source_path, options.output, kind)
    if kind == "book":
        converted = ssmdconvert.convert_book(
            source_path,
            options=ssmdconvert.BookConversionOptions(
                chapters=options.chapters,
                language=options.language,
            ),
            on_progress=on_progress,
        )
        ssmdconvert.write_book_bundle(
            converted.book,
            destination,
            format="directory",
            overwrite=options.force,
        )
        return resolve_workspace(destination)

    converted_document = ssmdconvert.convert_document_artifact(
        source_path,
        language=options.language,
    )
    ssmdconvert.write_document_bundle(
        converted_document,
        destination,
        format="directory",
        overwrite=options.force,
    )
    return resolve_workspace(destination)


def _workspace_destination(
    source: Path,
    output: Path | None,
    kind: WorkspaceKind,
) -> Path:
    suffix = ".ssmdoc" if kind == "document" else ".ssmdbook"
    if output is None:
        stem = (
            source.name[: -len(".ssmd.md")]
            if source.name.casefold().endswith(".ssmd.md")
            else source.stem
        )
        return source.with_name(f"{stem}{suffix}")

    destination = output.expanduser().resolve()
    name = destination.name.casefold()
    other_suffix = ".ssmdbook" if kind == "document" else ".ssmdoc"
    if name.endswith(other_suffix):
        raise ValueError(f"{kind} workspace output cannot end in {other_suffix}")
    if not name.endswith(suffix):
        destination = destination.with_name(destination.name + suffix)
    return destination
