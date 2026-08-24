from __future__ import annotations

import os
import shutil
import tempfile
from importlib import resources
from pathlib import Path

from .paths import safe_child

_RESOURCE_PACKAGE = "readio.resources.templates"


def packaged_template_names() -> tuple[str, ...]:
    root = resources.files(_RESOURCE_PACKAGE)
    return tuple(sorted(item.stem for item in root.iterdir() if item.name.endswith(".ssmd")))


def packaged_template(name: str) -> str:
    if name not in packaged_template_names():
        raise ValueError(f"unknown packaged template: {name}")
    resource = resources.files(_RESOURCE_PACKAGE).joinpath(f"{name}.ssmd")
    return resource.read_text(encoding="utf-8")


def template_filename(name: str) -> str:
    return name if Path(name).suffix.lower() == ".ssmd" else f"{name}.ssmd"


def template_path(directory: Path, name: str, *, require_exists: bool = True) -> Path:
    stem = Path(name).stem if Path(name).suffix.lower() == ".ssmd" else name
    path = safe_child(directory, template_filename(stem))
    if require_exists and not path.is_file():
        raise ValueError(f"template not found: {stem}")
    return path


def list_templates(directory: Path) -> list[str]:
    if not directory.exists():
        raise ValueError(f"configured template directory does not exist: {directory}")
    return sorted(
        path.stem for path in directory.iterdir() if path.is_file() and path.suffix == ".ssmd"
    )


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    os.close(fd)
    try:
        shutil.copyfile(source, temporary_name)
        os.replace(temporary_name, target)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def seed_templates(directory: Path, *, overwrite: bool = False) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in packaged_template_names():
        target = template_path(directory, name, require_exists=False)
        if overwrite or not target.exists():
            _atomic_write(target, packaged_template(name))


def show_template(directory: Path, name: str) -> str:
    return template_path(directory, name).read_text(encoding="utf-8")


def add_template(
    directory: Path,
    name: str,
    source: Path | None = None,
    *,
    content: str | None = None,
    force: bool = False,
) -> Path:
    target = template_path(directory, name, require_exists=False)
    if target.exists() and not force:
        raise ValueError(f"template already exists: {target.stem}; use --force to replace it")
    if content is None:
        if source is None:
            raise ValueError("template source is required")
        content = source.read_text(encoding="utf-8")
    if not content:
        raise ValueError("template source is empty")
    _atomic_write(target, content)
    return target


def remove_template(directory: Path, name: str) -> Path:
    target = template_path(directory, name)
    if not target.is_file() or target.is_symlink():
        raise ValueError(f"template not found: {Path(name).stem}")
    target.unlink()
    return target


def reset_template(directory: Path, name: str) -> Path:
    stem = Path(name).stem
    if stem not in packaged_template_names():
        raise ValueError(f"unknown packaged template: {stem}")
    target = template_path(directory, stem, require_exists=False)
    _atomic_write(target, packaged_template(stem))
    return target
