"""Small path-safety helpers for specialist-owned output files."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path


def save_specialist_file(
    path: str | Path,
    writer: Callable[[Path], object],
    *,
    force: bool = False,
) -> Path:
    """Use specialist persistence, staging only when explicit replacement is requested."""
    destination = Path(path).expanduser()
    exists = destination.exists() or destination.is_symlink()
    if exists and not force:
        raise FileExistsError(f"output already exists: {destination}")
    if exists and (destination.is_symlink() or not destination.is_file()):
        raise ValueError(f"refusing to replace non-file output: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not force:
        writer(destination)
        if not destination.is_file() or destination.is_symlink():
            raise OSError(f"specialist did not create an output file: {destination}")
        return destination
    with tempfile.TemporaryDirectory(prefix=".stage-output-", dir=destination.parent) as temporary:
        staged = Path(temporary) / destination.name
        writer(staged)
        if not staged.is_file() or staged.is_symlink():
            raise OSError(f"specialist did not create an output file: {staged}")
        os.replace(staged, destination)
    return destination


__all__ = ["save_specialist_file"]
