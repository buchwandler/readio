"""SSMDConvert-owned bundle workspace resolution and creation."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

import ssmdconvert  # type: ignore[import-not-found]

WorkspaceKind = Literal["document", "book"]
WorkspaceMode = Literal["temporary", "persistent"]


def default_bundle_workspace_path(source: str | Path) -> Path:
    """Return the canonical bundle destination chosen by SSMDConvert."""
    return Path(ssmdconvert.default_bundle_workspace_path(Path(source).expanduser()))


def inspect_source_kind(source: str | Path) -> WorkspaceKind:
    """Detect source kind through SSMDConvert's public inspection API."""
    kind = ssmdconvert.inspect_bundle_source(Path(source).expanduser()).kind
    if kind not in ("book", "document"):
        raise ValueError(f"SSMDConvert returned unsupported source kind {kind!r}")
    return cast(WorkspaceKind, kind)


@dataclass(frozen=True, slots=True)
class WorkspaceRef:
    """Small informational reference to a specialist-owned SSMD bundle."""

    root: Path
    kind: WorkspaceKind


@dataclass(frozen=True, slots=True)
class WorkspacePolicy:
    """Policy for choosing temporary or persistent workflow storage without creating it."""

    mode: WorkspaceMode
    root: Path
    source_is_bundle: bool
    newly_created: bool
    cleanup: bool


@dataclass(frozen=True, slots=True)
class CreateWorkspaceRequest:
    """Options for converting a source into an SSMD bundle."""

    source: Path
    output: Path | None = None
    language: str | None = None
    chapters: str = "all"
    force: bool = False


def resolve_workspace(path: str | Path) -> WorkspaceRef:
    """Load and validate an editable `.ssmd-bundle` directory."""

    root = Path(path).expanduser().resolve()
    name = root.name.casefold()
    if name.endswith(".ssmd-bundle.zip"):
        raise ValueError(
            "Readio requires an editable .ssmd-bundle directory; packed .ssmd-bundle.zip "
            "artifacts are not pipeline workspaces"
        )
    if not name.endswith(".ssmd-bundle"):
        raise ValueError("workspace path must end in .ssmd-bundle")
    if not root.exists():
        raise FileNotFoundError(root)
    if not root.is_dir():
        raise ValueError("Readio requires an editable .ssmd-bundle directory")

    native = ssmdconvert.load_bundle_workspace(root)
    return WorkspaceRef(root=root, kind=native.kind)


def create_workspace(
    source: str | Path,
    request: CreateWorkspaceRequest | None = None,
    *,
    on_progress: Callable[[Any], None] | None = None,
) -> WorkspaceRef:
    """Convert a source into a bundle, or resolve an existing bundle."""

    source_path = Path(source).expanduser().resolve()
    options = request or CreateWorkspaceRequest(source=source_path)
    if Path(options.source).expanduser().resolve() != source_path:
        raise ValueError("request.source must match the source argument")
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    if source_path.name.casefold().endswith((".ssmd-bundle", ".ssmd-bundle.zip")):
        return resolve_workspace(source_path)

    result = ssmdconvert.convert_bundle(
        source_path,
        output=options.output,
        chapters=options.chapters,
        language=options.language,
        force=options.force,
        on_progress=on_progress,
    )
    return resolve_workspace(result.output)


def resolve_workspace_policy(
    source: str | Path, *, bundle: str | Path | None = None
) -> WorkspacePolicy:
    """Resolve storage policy without creating a temporary directory or bundle.

    An existing bundle used as SOURCE always wins. An explicit bundle path selects
    persistent storage; otherwise source files use temporary storage until execution.
    """
    source_path = Path(source).expanduser().resolve()
    source_name = source_path.name.casefold()
    if source_name.endswith((".ssmd-bundle", ".ssmd-bundle.zip")):
        workspace = resolve_workspace(source_path)
        return WorkspacePolicy(
            mode="persistent",
            root=workspace.root,
            source_is_bundle=True,
            newly_created=False,
            cleanup=False,
        )
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    if not source_path.is_file():
        raise ValueError("source must be a file or an editable .ssmd-bundle directory")
    if bundle is None:
        temporary_root = Path(tempfile.gettempdir()) / f"readio-{uuid4().hex}.ssmd-bundle"
        return WorkspacePolicy(
            mode="temporary",
            root=temporary_root,
            source_is_bundle=False,
            newly_created=True,
            cleanup=True,
        )

    candidate = Path(bundle).expanduser()
    if candidate.is_symlink():
        raise ValueError("persistent bundle path must not be a symlink")
    root = candidate.resolve(strict=False)
    name = root.name.casefold()
    if name.endswith(".ssmd-bundle.zip") or not name.endswith(".ssmd-bundle"):
        raise ValueError("persistent bundle path must end in .ssmd-bundle")
    if root.exists():
        workspace = resolve_workspace(root)
        root = workspace.root
        newly_created = False
    else:
        newly_created = True
    return WorkspacePolicy(
        mode="persistent",
        root=root,
        source_is_bundle=False,
        newly_created=newly_created,
        cleanup=False,
    )
