"""Engine-neutral selection of persisted UtterPlan units and segments."""

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
    segment_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScopedUnitSelection:
    scope_id: str
    unit_indices: tuple[int, ...]
    segment_ids: tuple[str, ...]



@dataclass(frozen=True, slots=True)
class ProjectUnitSelection:
    scopes: tuple[ScopedUnitSelection, ...]
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


def _with_segments(plan: Any, unit_indices: tuple[int, ...], description: str) -> UnitSelection:
    units = {int(unit.index): unit for unit in getattr(plan, "units", ())}
    segment_ids: list[str] = []
    seen: set[str] = set()
    for index in unit_indices:
        for segment_id in tuple(getattr(units[index], "segment_ids", ()) or ()):
            if segment_id not in seen:
                seen.add(segment_id)
                segment_ids.append(str(segment_id))
    return UnitSelection(unit_indices, description, tuple(segment_ids))


def resolve_unit_selection(plan: Any, selector: str) -> UnitSelection:
    units = _ensure_units(plan)
    normalized = (selector or "all").strip().lower()
    if normalized == "all":
        return _with_segments(
            plan, tuple(int(unit.index) for unit in units), "all"
        )
    if normalized in {"last-paragraph", "last:paragraph"}:
        paragraphs = [_paragraphs_for_unit(plan, unit) for unit in units]
        last = max((value for values in paragraphs for value in values), default=0)
        selected = tuple(
            int(unit.index) for unit, values in zip(units, paragraphs) if last in values
        )
        if not selected:
            raise SelectionError("the plan contains no last paragraph")
        return _with_segments(plan, selected, "last-paragraph")
    if ":" not in normalized:
        raise SelectionError(
            "selector must be all, first:N, last:N, paragraph:N[-M], sentence:N[-M], or unit:N[-M]"
        )
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
        indices = tuple(int(unit.index) for unit in selected_units)
        return _with_segments(plan, indices, normalized)
    if kind not in {"paragraph", "sentence"}:
        raise SelectionError(f"unknown selector kind: {kind}")
    start, end = _range(raw, kind)
    if kind == "sentence" and all(getattr(unit, "kind", "") == "sentence" for unit in units):
        selected_units = units[start - 1 : end]
        if not selected_units:
            raise SelectionError(
                f"sentence {start} is out of range; plan has {len(units)} sentences"
            )
        indices = tuple(int(unit.index) for unit in selected_units)
        return _with_segments(plan, indices, normalized)
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
    return _with_segments(plan, tuple(selected), normalized)


def _project_scope_groups(plan: Any, kind: str) -> list[tuple[int, tuple[int, ...]]]:
    units = tuple(getattr(plan, "units", ()))
    if not units:
        return []
    if kind == "sentence" and all(
        getattr(unit, "kind", "") == "sentence" for unit in units
    ):
        return [(int(unit.index), (int(unit.index),)) for unit in units]

    segments = {str(segment.id): segment for segment in getattr(plan, "segments", ())}
    members: dict[int, list[int]] = {}
    ordered_values: list[int] = []
    for unit in units:
        unit_index = int(unit.index)
        values: list[int] = []
        for segment_id in unit.segment_ids:
            key = str(segment_id)
            if key in segments:
                value = int(getattr(segments[key], kind, 0))
                if value not in values:
                    values.append(value)
        for value in values:
            if value not in members:
                members[value] = []
                ordered_values.append(value)
            if unit_index not in members[value]:
                members[value].append(unit_index)
    return [(value, tuple(members[value])) for value in ordered_values]



def resolve_project_selection(
    plan_scopes: tuple[tuple[Any, Any], ...], selector: str
) -> ProjectUnitSelection:
    """Resolve a selector over ordered ``(scope, plan)`` pairs."""
    if not plan_scopes:
        raise SelectionError("the project contains no plan scopes")
    scopes = tuple((str(getattr(scope, "id", scope)), plan) for scope, plan in plan_scopes)
    if len(scopes) == 1:
        scope_id, plan = scopes[0]
        selection = resolve_unit_selection(plan, selector)
        return ProjectUnitSelection(
            scopes=(
                ScopedUnitSelection(
                    scope_id=scope_id,
                    unit_indices=selection.unit_indices,
                    segment_ids=selection.segment_ids,
                ),
            ),
            description=selection.description,
        )

    normalized = (selector or "all").strip().lower()
    entries = [
        (scope_id, plan, unit)
        for scope_id, plan in scopes
        for unit in tuple(getattr(plan, "units", ()))
    ]
    if not entries:
        raise SelectionError("the project contains no readable units")

    selected_entries: list[tuple[str, Any, Any]]
    if normalized == "all":
        selected_entries = entries
    elif normalized in {"last-paragraph", "last:paragraph"}:
        groups = [
            (scope_id, unit_indices)
            for scope_id, plan in scopes
            for _, unit_indices in _project_scope_groups(plan, "paragraph")
        ]
        if not groups:
            raise SelectionError("the project contains no last paragraph")
        selected_groups = {(groups[-1][0], index) for index in groups[-1][1]}
        selected_entries = [
            item
            for item in entries
            if (item[0], int(item[2].index)) in selected_groups
        ]
        normalized = "last-paragraph"
    elif ":" not in normalized:
        raise SelectionError(
            "selector must be all, first:N, last:N, paragraph:N[-M], sentence:N[-M], "
            "or unit:N[-M]"
        )
    else:
        kind, raw = normalized.split(":", 1)
        if kind in {"first", "last", "unit"}:
            start, end = _range(raw, kind)
            if kind == "first":
                selected_entries = entries[:end]
            elif kind == "last":
                selected_entries = entries[-end:]
            else:
                if end > len(entries):
                    raise SelectionError(
                        f"unit {end} is out of range; project has {len(entries)} units"
                    )
                selected_entries = entries[start - 1 : end]
        elif kind in {"paragraph", "sentence"}:
            start, end = _range(raw, kind)
            groups = [
                (scope_id, unit_indices)
                for scope_id, plan in scopes
                for _, unit_indices in _project_scope_groups(plan, kind)
            ]
            if end > len(groups):
                raise SelectionError(
                    f"{kind} {end} is out of range; project has {len(groups)} {kind}s"
                )
            selected_groups = {
                (scope_id, unit_index)
                for scope_id, unit_indices in groups[start - 1 : end]
                for unit_index in unit_indices
            }
            selected_entries = [
                item
                for item in entries
                if (item[0], int(item[2].index)) in selected_groups
            ]
        else:
            raise SelectionError(f"unknown selector kind: {kind}")

    selected_by_scope: dict[str, list[int]] = {scope_id: [] for scope_id, _ in scopes}
    segments_by_scope: dict[str, list[str]] = {scope_id: [] for scope_id, _ in scopes}
    seen_units: dict[str, set[int]] = {scope_id: set() for scope_id, _ in scopes}
    seen_segments: dict[str, set[str]] = {scope_id: set() for scope_id, _ in scopes}
    for scope_id, _, unit in selected_entries:
        unit_index = int(unit.index)
        if unit_index not in seen_units[scope_id]:
            seen_units[scope_id].add(unit_index)
            selected_by_scope[scope_id].append(unit_index)
        for segment_id in unit.segment_ids:
            segment_id = str(segment_id)
            if segment_id not in seen_segments[scope_id]:
                seen_segments[scope_id].add(segment_id)
                segments_by_scope[scope_id].append(segment_id)
    return ProjectUnitSelection(
        scopes=tuple(
            ScopedUnitSelection(
                scope_id=scope_id,
                unit_indices=tuple(selected_by_scope[scope_id]),
                segment_ids=tuple(segments_by_scope[scope_id]),
            )
            for scope_id, _ in scopes
        ),
        description=normalized,
    )




__all__ = [
    "ProjectUnitSelection",
    "ScopedUnitSelection",
    "SelectionError",
    "UnitSelection",
    "resolve_project_selection",
    "resolve_unit_selection",
]
