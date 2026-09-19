"""Engine-neutral selection of persisted UtterancePlan units."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


class SelectionError(ValueError):
    """Raised when a unit selector is invalid or selects nothing."""


@dataclass(frozen=True, slots=True)
class UnitSelection:
    unit_indices: tuple[int, ...]
    description: str


def _range(value: str, label: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)(?:-(\d+))?", value)
    if not match:
        raise SelectionError(f"{label} selector must look like {label}:3 or {label}:3-5")
    start = int(match.group(1))
    end = int(match.group(2) or start)
    if start <= 0 or end < start:
        raise SelectionError(f"{label} selector values must be positive and ordered")
    return start, end


def _ensure_units(plan: Any) -> tuple[Any, ...]:
    units = tuple(getattr(plan, "units", ()))
    if not units:
        raise SelectionError("the plan contains no readable units")
    return units


def _paragraphs_for_unit(plan: Any, unit: Any) -> set[int]:
    by_id = {segment.id: segment for segment in getattr(plan, "segments", ())}
    return {int(getattr(by_id[sid], "paragraph", 0)) for sid in unit.segment_ids if sid in by_id}


def resolve_unit_selection(plan: Any, selector: str) -> UnitSelection:
    units = _ensure_units(plan)
    normalized = (selector or "all").strip().lower()
    if normalized == "all":
        return UnitSelection(tuple(int(unit.index) for unit in units), "all")
    if normalized in {"last-paragraph", "last:paragraph"}:
        paragraphs = [_paragraphs_for_unit(plan, unit) for unit in units]
        last = max((value for values in paragraphs for value in values), default=0)
        selected = tuple(int(unit.index) for unit, values in zip(units, paragraphs) if last in values)
        if not selected:
            raise SelectionError("the plan contains no last paragraph")
        return UnitSelection(selected, "last-paragraph")
    if ":" not in normalized:
        raise SelectionError("selector must be all, first:N, last:N, paragraph:N[-M], sentence:N[-M], or unit:N[-M]")
    kind, raw = normalized.split(":", 1)
    if kind in {"first", "last", "unit"}:
        start, end = _range(raw, kind)
        if kind == "first":
            selected_units = units[:end]
        elif kind == "last":
            selected_units = units[-end:]
        else:
            if end > len(units):
                raise SelectionError(f"unit {end} is out of range; plan has {len(units)} units")
            selected_units = units[start - 1 : end]
        if not selected_units:
            raise SelectionError("selector selected no units")
        return UnitSelection(tuple(int(unit.index) for unit in selected_units), normalized)
    if kind not in {"paragraph", "sentence"}:
        raise SelectionError(f"unknown selector kind: {kind}")
    start, end = _range(raw, kind)
    if kind == "sentence" and all(getattr(unit, "kind", "") == "sentence" for unit in units):
        selected_units = units[start - 1 : end]
        if not selected_units:
            raise SelectionError(f"sentence {start} is out of range; plan has {len(units)} sentences")
        return UnitSelection(tuple(int(unit.index) for unit in selected_units), normalized)
    selected: list[int] = []
    by_id = {segment.id: segment for segment in getattr(plan, "segments", ())}
    for unit in units:
        values = {
            int(getattr(by_id[sid], kind, 0)) + 1
            for sid in unit.segment_ids
            if sid in by_id
        }
        if any(start <= value <= end for value in values):
            selected.append(int(unit.index))
    if not selected:
        maximum = max(
            (int(getattr(segment, kind, 0)) + 1 for segment in getattr(plan, "segments", ())),
            default=0,
        )
        raise SelectionError(f"{kind} {start} is out of range; plan has {maximum} {kind}s")
    return UnitSelection(tuple(selected), normalized)


__all__ = ["SelectionError", "UnitSelection", "resolve_unit_selection"]
