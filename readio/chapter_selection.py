"""Parsing for stable, 1-based EPUB chapter selection."""

from __future__ import annotations

import re


def parse_chapter_selection(spec: str | None, total: int) -> tuple[int, ...]:
    """Return selected 0-based chapter indices in source order."""
    if total < 1:
        raise ValueError("no chapters are available for selection")
    value = "all" if spec is None else spec.strip()
    if not value:
        raise ValueError("chapter selection is empty")
    parts = [part.strip() for part in value.split(",")]
    if any(not part for part in parts):
        raise ValueError("chapter selection is empty")
    if any(part.lower() == "all" for part in parts):
        if len(parts) != 1:
            raise ValueError("'all' must be used alone")
        return tuple(range(total))

    selected: set[int] = set()
    for part in parts:
        match = re.fullmatch(r"(\d+)\s*(?:-\s*(\d+))?", part)
        if match is None:
            raise ValueError(f"invalid chapter selection {part!r}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < 1:
            raise ValueError("chapter numbers must be positive")
        if end < start:
            raise ValueError(f"invalid chapter selection {part!r}: range start must be <= end")
        if start > total or end > total:
            number = start if start > total else end
            raise ValueError(f"chapter {number} is out of range; EPUB has {total} chapters")
        selected.update(range(start - 1, end))

    if not selected:
        raise ValueError("chapter selection is empty")
    return tuple(index for index in range(total) if index in selected)


__all__ = ["parse_chapter_selection"]
