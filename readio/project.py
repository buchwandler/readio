"""Filesystem and persistence helpers for Readio projects."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .document import InputDocument, infer_input_format
from .project_model import PlanIndex, ProjectFormatError, ProjectManifest


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


def _safe_relative(project_root: Path, path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ProjectFormatError(f"project path must be relative and contained: {path!r}")
    resolved = (project_root / candidate).resolve()
    root = project_root.resolve()
    if resolved != root and root not in resolved.parents:
        raise ProjectFormatError(f"project path escapes project root: {path!r}")
    return project_root / candidate


def project_paths(root: Path) -> dict[str, Path]:
    root = root.expanduser().resolve()
    manifest = ProjectManifest.from_dict(read_json(root / "project.json"))
    paths = {
        "root": root,
        "project": root / "project.json",
        "source": _safe_relative(root, manifest.source_path),
        "document_text": _safe_relative(root, manifest.document_text_path),
        "document_metadata": _safe_relative(root, manifest.document_metadata_path),
        "plan_index": _safe_relative(root, manifest.plan_index_path),
        "synthesis_profile": _safe_relative(root, manifest.synthesis_profile_path),
        "synthesis_trace": _safe_relative(root, manifest.synthesis_trace_path),
        "composition_audiojob": _safe_relative(root, manifest.composition_audiojob_path),
        "composition_state": _safe_relative(root, manifest.composition_state_path),
        "composition_master": _safe_relative(root, manifest.composition_master_path),
        "composition_timeline": _safe_relative(root, manifest.composition_timeline_path),
        "lock": root / ".lock",
    }
    return paths


class Project:
    def __init__(self, root: Path, manifest: ProjectManifest) -> None:
        self.root = root.resolve()
        self.manifest = manifest

    @property
    def paths(self) -> dict[str, Path]:
        return project_paths(self.root)

    def path(self, relative: str) -> Path:
        return _safe_relative(self.root, relative)

    def load_plan_index(self) -> PlanIndex:
        return PlanIndex.from_dict(read_json(self.paths["plan_index"]))

    def document(self) -> InputDocument:
        metadata = read_json(self.paths["document_metadata"])
        input_format = metadata.get("input_format", self.manifest.source_format)
        document_format = metadata.get("document_format")
        if document_format is None:
            # Legacy snapshots were normalized to text except for SSMD, which
            # must remain available to the semantic SSMD bridge.
            document_format = "ssmd" if input_format == "ssmd" else "text"
        return InputDocument(
            text=self.paths["document_text"].read_text(encoding="utf-8"),
            source_path=self.paths["source"],
            format=document_format,
        )


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
        if (directory / "project.json").is_file():
            return directory
    return None


def load_project(path: Path | str | None = None) -> Project:
    root = find_project(path)
    if root is None:
        raise ProjectError(f"not a Readio project: {path or Path.cwd()}")
    manifest = ProjectManifest.from_dict(read_json(root / "project.json"))
    # Validate every manifest path even before a stage tries to use it.
    for relative in (
        manifest.source_path,
        manifest.document_metadata_path,
        manifest.document_text_path,
        manifest.plan_index_path,
        manifest.synthesis_profile_path,
        manifest.synthesis_trace_path,
        manifest.composition_audiojob_path,
        manifest.composition_state_path,
        manifest.composition_master_path,
        manifest.composition_timeline_path,
    ):
        _safe_relative(root, relative)
    return Project(root, manifest)


def init_project(source: Path | str, output: Path | str | None = None) -> Project:
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise ProjectError(f"source is not a regular file: {source_path}")
    root = Path(output or f"{source_path.stem}.readio").expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    root = root.resolve()
    if root.exists():
        raise ProjectError(f"project destination already exists: {root}")
    temporary = root.with_name(f".{root.name}.tmp-{secrets.token_hex(8)}")
    source_name = source_path.name
    source_relative = Path("source") / source_name
    source_sha = hash_file(source_path)
    document_text = source_path.read_text(encoding="utf-8")
    input_format = infer_input_format(source_path)
    project_payload = {
        "name": root.stem,
        "source": {
            "path": source_relative.as_posix(),
            "format": input_format,
            "sha256": source_sha,
        },
    }
    project_id = f"sha256:{sha256_bytes(canonical_json(project_payload))}"
    manifest = ProjectManifest(
        project_id=project_id,
        name=root.stem,
        source_path=source_relative.as_posix(),
        source_format=input_format,
        source_sha256=source_sha,
    )
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
            temporary / manifest.document_metadata_path,
            {
                "format": "readio.document",
                "schema_version": 1,
                "source_sha256": source_sha,
                "document_sha256": sha256_bytes(document_text.encode("utf-8")),
                "input_format": input_format,
                "document_format": "ssmd" if input_format == "ssmd" else "text",
                "source_path": f"../{source_relative.as_posix()}",
            },
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
]
