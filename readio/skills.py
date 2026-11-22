"""Access packaged Readio agent resources."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def skill_path() -> Path:
    """Return the installed Readio Skill resource path."""
    resource = files("readio").joinpath("skills", "readio", "SKILL.md")
    if not resource.is_file():
        raise FileNotFoundError("The packaged Readio Skill is missing")
    return Path(str(resource))


__all__ = ["skill_path"]
