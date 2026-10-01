"""Parsing for stable, 1-based book chapter selection."""

from __future__ import annotations

import re


def parse_chapter_selection(
    spec: str | None,
    *,
    available_numbers: tuple[int, ...],
) -> tuple[int, ...]:
    """Return selected source chapter numbers in available source order."""
    if not available_numbers:
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
        return available_numbers

    available = set(available_numbers)
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
        requested = set(range(start, end + 1))
        missing = requested - available
        if missing:
            number = min(missing)
            raise ValueError(f"chapter {number} is not available in this book source")
        selected.update(requested)

    if not selected:
        raise ValueError("chapter selection is empty")
    return tuple(number for number in available_numbers if number in selected)


__all__ = ["parse_chapter_selection"]
