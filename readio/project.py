"""Filesystem and persistence helpers for Readio projects."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from .document import DocumentProvenance, InputDocument, InputFormat
from .integrations.ssmdconvert import convert_document_source
from .jsonutil import json_value
from .project_model import (
    DocumentIndex,
    DocumentScope,
    PlanIndex,
    ProjectFormatError,
    ProjectManifest,
)


class ProjectError(ValueError):
    """Raised for invalid or unsafe project operations."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(8)}")
    try:
        with temporary.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(
        path,
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n",
    )


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectFormatError(f"cannot read JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProjectFormatError(f"JSON artifact {path} must contain an object")
    return value


def _safe_workspace_relative(workspace_root: Path, path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in path:
        raise ProjectFormatError(f"workspace path must be relative and contained: {path!r}")
    resolved = (workspace_root / candidate).resolve()
    root = workspace_root.resolve()
    if resolved != root and root not in resolved.parents:
        raise ProjectFormatError(f"workspace path escapes workspace root: {path!r}")
    return workspace_root / candidate


def _safe_state_relative(state_root: Path, path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in path:
        raise ProjectFormatError(f"project path must be relative and contained: {path!r}")
    resolved = (state_root / candidate).resolve()
    root = state_root.resolve()
    if resolved != root and root not in resolved.parents:
        raise ProjectFormatError(f"project path escapes state root: {path!r}")
    return state_root / candidate


def _safe_relative(project_root: Path, path: str) -> Path:
    """Backward-compatible alias for resolving Readio state-relative paths."""
    return _safe_state_relative(project_root, path)


def project_paths(root: Path, *, state_root: Path | None = None) -> dict[str, Path]:
    workspace_root = root.expanduser().resolve()
    if state_root is None:
        attached_root = workspace_root / ".readio"
        state_root = attached_root if (attached_root / "project.json").is_file() else workspace_root
    state_root = state_root.expanduser().resolve()
    manifest = ProjectManifest.from_dict(read_json(state_root / "project.json"))
    if manifest.schema_version == 4:
        attached_state = workspace_root / ".readio"
        if attached_state.is_symlink() or state_root != attached_state:
            raise ProjectFormatError("attached project state must be stored in workspace/.readio")
    state_path = lambda relative: _safe_state_relative(state_root, relative)
    workspace_path = lambda relative: _safe_workspace_relative(workspace_root, relative)
    source_path = workspace_path(manifest.source_path)
    if manifest.schema_version == 4 and (
        source_path.resolve() == state_root or state_root in source_path.resolve().parents
    ):
        raise ProjectFormatError("attached workspace source path must not point inside .readio")
    return {
        "root": workspace_root,
        "workspace_root": workspace_root,
        "state_root": state_root,
        "project": state_root / "project.json",
        "source": source_path,
        "document_text": state_path(manifest.document_text_path),
        "document_metadata": state_path(manifest.document_metadata_path),
        "document_index": state_path(manifest.document_index_path),
        "plan_index": state_path(manifest.plan_index_path),
        "synthesis_profile": state_path(manifest.synthesis_profile_path),
        "synthesis_trace": state_path(manifest.synthesis_trace_path),
        "composition_audiojob": state_path(manifest.composition_audiojob_path),
        "composition_state": state_path(manifest.composition_state_path),
        "composition_master": state_path(manifest.composition_master_path),
        "composition_timeline": state_path(manifest.composition_timeline_path),
        "lock": state_root / ".lock",
    }


class Project:
    def __init__(
        self, root: Path, manifest: ProjectManifest, state_root: Path | None = None
    ) -> None:
        self.workspace_root = root.resolve()
        self.root = self.workspace_root
        if manifest.schema_version == 4:
            attached_state_root = self.workspace_root / ".readio"
            _safe_workspace_relative(self.workspace_root, ".readio")
            if attached_state_root.is_symlink():
                raise ProjectFormatError("attached project state root must not be a symlink")
            requested_state_root = (state_root or attached_state_root).expanduser().absolute()
            if requested_state_root != attached_state_root:
                raise ProjectFormatError("attached project state root must be workspace/.readio")
            self.state_root = attached_state_root
        else:
            if state_root is not None and state_root.resolve() != self.workspace_root:
                raise ProjectFormatError(
                    "standalone project state root must equal its workspace root"
                )
            self.state_root = self.workspace_root
        self.manifest = manifest

    @property
    def paths(self) -> dict[str, Path]:
        return project_paths(self.workspace_root, state_root=self.state_root)

    def path(self, relative: str) -> Path:
        return self.state_path(relative)

    def state_path(self, relative: str) -> Path:
        return _safe_state_relative(self.state_root, relative)

    def workspace_path(self, relative: str) -> Path:
        path = _safe_workspace_relative(self.workspace_root, relative)
        if self.manifest.schema_version == 4 and (
            path.resolve() == self.state_root or self.state_root in path.resolve().parents
        ):
            raise ProjectFormatError("attached workspace path must not point inside .readio")
        return path

    def load_plan_index(self) -> PlanIndex:
        return PlanIndex.from_dict(read_json(self.paths["plan_index"]))

    def load_document_index(self) -> DocumentIndex:
        return DocumentIndex.from_dict(read_json(self.paths["document_index"]))

    def document_scopes(self) -> tuple[DocumentScope, ...]:
        return self.load_document_index().scopes

    def load_document_scope(self, scope: DocumentScope) -> InputDocument:
        index = self.load_document_index()
        indexed = next((item for item in index.scopes if item.id == scope.id), None)
        if indexed is None:
            raise KeyError(f"document scope is not indexed: {scope.id}")
        path = (
            self.workspace_path(indexed.path)
            if self.manifest.schema_version == 4
            else self.state_path(indexed.path)
        )
        provenance = (
            DocumentProvenance(
                source_format="ssmd",
                media_type="text/markdown",
                source_name=path.name,
                metadata=index.metadata,
            )
            if self.manifest.schema_version == 4
            else None
        )
        return InputDocument(
            text=path.read_text(encoding="utf-8"),
            source_path=path,
            format=cast(InputFormat, indexed.input_format),
            provenance=provenance,
            canonical_sha256=hash_file(path) if indexed.input_format.casefold() == "ssmd" else None,
        )

    def document(self) -> InputDocument:
        scopes = self.document_scopes()
        if len(scopes) != 1:
            raise ProjectError(
                "project has multiple document scopes; use load_document_scope(scope)"
            )
        return self.load_document_scope(scopes[0])


@contextmanager
def project_lock(project: Project, *, operation: str = "mutation") -> Iterator[None]:
    lock_path = project.paths["lock"]
    payload = f"{os.getpid()} {operation}\n".encode()
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        owner = ""
        try:
            owner = lock_path.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        raise ProjectError(
            f"project is locked for another mutation ({owner or 'unknown owner'})"
        ) from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def find_project(path: Path | str | None = None) -> Path | None:
    candidate = Path(path or Path.cwd()).expanduser()
    if candidate.is_file():
        candidate = candidate.parent
    candidate = candidate.resolve()
    for directory in (candidate, *candidate.parents):
        if directory.name == ".readio":
            continue
        attached_project = directory / ".readio" / "project.json"
        if attached_project.is_file():
            return directory
        if (directory / "project.json").is_file():
            return directory
    return None


def load_project(path: Path | str | None = None) -> Project:
    root = find_project(path)
    if root is None:
        raise ProjectError(f"not a Readio project: {path or Path.cwd()}")
    attached_state_root = root / ".readio"
    has_attached_project = (attached_state_root / "project.json").is_file()
    if has_attached_project and attached_state_root.is_symlink():
        raise ProjectFormatError("attached project state root must not be a symlink")
    state_root = attached_state_root if has_attached_project else root
    manifest = ProjectManifest.from_dict(read_json(state_root / "project.json"))
    if manifest.schema_version == 4 and state_root != attached_state_root:
        raise ProjectFormatError("attached schema-v4 project must store state in workspace/.readio")
    # State fields are always relative to the state root; canonical source and
    # chapter fields are independently resolved from the workspace root.
    for relative in (
        manifest.document_metadata_path,
        manifest.document_text_path,
        manifest.plan_index_path,
        manifest.document_index_path,
        manifest.synthesis_profile_path,
        manifest.synthesis_trace_path,
        manifest.composition_audiojob_path,
        manifest.composition_state_path,
        manifest.composition_master_path,
        manifest.composition_timeline_path,
    ):
        _safe_state_relative(state_root, relative)
    source_path = _safe_workspace_relative(root, manifest.source_path)
    if manifest.schema_version == 4 and (
        source_path.resolve() == state_root or state_root in source_path.resolve().parents
    ):
        raise ProjectFormatError("attached workspace source path must not point inside .readio")
    return Project(root, manifest, state_root=state_root)


def update_project_manifest(
    project: Project,
    update: Callable[[ProjectManifest], ProjectManifest],
    *,
    operation: str = "manifest-update",
) -> Project:
    """Atomically update project.json under the project's mutation lock."""
    with project_lock(project, operation=operation):
        current = load_project(project.root)
        manifest = update(current.manifest)
        atomic_write_json(current.paths["project"], manifest.to_dict())
    return load_project(project.root)


def init_project(source: Path | str, output: Path | str | None = None) -> Project:
    source_path = Path(source).expanduser().resolve()
    root = Path(output or f"{source_path.stem}.readio").expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    root = root.resolve()
    if root.exists():
        raise ProjectError(f"project destination already exists: {root}")

    converted = convert_document_source(source_path)
    document_text = converted.ssmd
    document_sha = sha256_bytes(document_text.encode("utf-8"))
    title_value = converted.metadata.get("title")
    title = title_value if isinstance(title_value, str) and title_value else root.stem
    source_name = source_path.name
    source_relative = Path("source") / source_name
    source_sha = hash_file(source_path)
    source_format = converted.source_format
    index_metadata = cast(
        dict[str, Any],
        json_value(
            {
                "title": title,
                "author": converted.metadata.get("author"),
                "language": converted.metadata.get("language"),
                "conversion": {
                    "tool": "ssmdconvert",
                    "version": converted.converter_version,
                    "source_format": source_format,
                    "media_type": converted.media_type,
                    "source_name": converted.source_name,
                },
            }
        ),
    )
    project_payload = {
        "name": root.stem,
        "source": {
            "path": source_relative.as_posix(),
            "format": source_format,
            "sha256": source_sha,
        },
    }
    project_id = f"sha256:{sha256_bytes(canonical_json(project_payload))}"
    manifest = ProjectManifest(
        project_id=project_id,
        name=root.stem,
        source_path=source_relative.as_posix(),
        source_format=source_format,
        source_sha256=source_sha,
    )
    temporary = root.with_name(f".{root.name}.tmp-{secrets.token_hex(8)}")
    try:
        for directory in (
            "source",
            "document",
            "plan",
            "synthesis/segments",
            "synthesis/cache",
            "composition/parts",
            "output",
            "report",
        ):
            (temporary / directory).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, temporary / source_relative)
        (temporary / manifest.document_text_path).write_text(document_text, encoding="utf-8")
        atomic_write_json(
            temporary / manifest.document_index_path,
            DocumentIndex(
                scopes=(
                    DocumentScope(
                        id="document",
                        kind="document",
                        path=manifest.document_text_path,
                        input_format="ssmd",
                        title=title,
                        extracted_sha256=document_sha,
                    ),
                ),
                metadata=index_metadata,
            ).to_dict(),
        )
        atomic_write_json(temporary / "project.json", manifest.to_dict())
        os.replace(temporary, root)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return load_project(root)


__all__ = [
    "Project",
    "ProjectError",
    "atomic_write_bytes",
    "atomic_write_json",
    "canonical_json",
    "find_project",
    "hash_file",
    "init_project",
    "load_project",
    "project_lock",
    "project_paths",
    "read_json",
    "sha256_bytes",
    "update_project_manifest",
]
