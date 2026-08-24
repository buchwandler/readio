from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from .paths import automatic_ingest_name, safe_child
from .templates import template_path


def ingest_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    os.close(fd)
    try:
        shutil.copyfile(source, temporary_name)
        os.replace(temporary_name, target)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def new_ingest(
    directory: Path,
    *,
    name: str | None = None,
    template_directory: Path | None = None,
    template: str | None = None,
) -> Path:
    if template is not None:
        if template_directory is None:
            raise ValueError("template directory is required")
        source = template_path(template_directory, template)
        requested_name = name or automatic_ingest_name(template=template, suffix=".ssmd")
        target = safe_child(directory, requested_name)
        if target.exists():
            if name:
                raise ValueError(f"ingest file already exists: {target}")
            return new_ingest(
                directory,
                name=automatic_ingest_name(template=template, suffix=".ssmd"),
                template_directory=template_directory,
                template=template,
            )
        _atomic_copy(source, target)
        return target
    requested_name = name or automatic_ingest_name(suffix=".txt")
    target = safe_child(directory, requested_name)
    if target.exists():
        if name:
            raise ValueError(f"ingest file already exists: {target}")
        return new_ingest(directory)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")
    return target


def list_ingest(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        (path for path in directory.iterdir() if path.is_file()), key=lambda path: path.name
    )
