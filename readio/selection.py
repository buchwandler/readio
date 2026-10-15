"""Engine-neutral selection of UtterPlan v5 flows and source segments."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


class SelectionError(ValueError):
    """Raised when a flow selector is invalid or selects nothing."""


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


@dataclass(frozen=True, slots=True)
class _FlowUnit:
    index: int
    id: str
    kind: str
    segment_ids: tuple[str, ...]


def flow_id(index: int, flow: Any) -> str:
    digest = str(flow.content_hash).removeprefix("sha256:")
    return f"flow-{index:04d}-{digest[:12]}"


def flow_segment_id(index: int, flow: Any, segment_index: int) -> str:
    return f"{flow_id(index, flow)}:segment-{segment_index:04d}"


def _flow_units(plan: Any) -> tuple[_FlowUnit, ...]:
    """Expose atomic flow segments as the user-selectable project units."""
    if not hasattr(plan, "flow"):
        units = tuple(getattr(plan, "units", ()))
        return tuple(
            _FlowUnit(
                index=index,
                id=str(getattr(unit, "id", f"unit-{index:04d}")),
                kind=str(getattr(unit, "kind", "unit")),
                segment_ids=tuple(getattr(unit, "segment_ids", ())),
            )
            for index, unit in enumerate(units)
        )
    units = []
    for flow_index, flow in enumerate(plan.flow):
        for segment_index, _segment in enumerate(flow.segments):
            identity = flow_segment_id(flow_index, flow, segment_index)
            units.append(
                _FlowUnit(
                    index=len(units),
                    id=identity,
                    kind="segment",
                    segment_ids=(identity,),
                )
            )
    return tuple(units)


def _range(value: str, label: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)(?:-(\d+))?", value)
    if not match:
        raise SelectionError(f"{label} selector must look like {label}:3 or {label}:3-5")
    start = int(match.group(1))
    end = int(match.group(2) or start)
    if start <= 0 or end < start:
        raise SelectionError(f"{label} selector values must be positive and ordered")
    return start, end


def _with_segments(plan: Any, indices: tuple[int, ...], description: str) -> UnitSelection:
    units = {unit.index: unit for unit in _flow_units(plan)}
    segment_ids: list[str] = []
    seen: set[str] = set()
    for index in indices:
        for segment_id in units[index].segment_ids:
            if segment_id not in seen:
                seen.add(segment_id)
                segment_ids.append(segment_id)
    return UnitSelection(indices, description, tuple(segment_ids))


def _groups(plan: Any, kind: str) -> list[tuple[int, tuple[int, ...]]]:
    """Return ordered logical groups as selectable segment-unit indices."""
    units = _flow_units(plan)
    if not units:
        return []
    if not hasattr(plan, "flow"):
        segments = {
            str(getattr(segment, "id", "")): segment for segment in getattr(plan, "segments", ())
        }
        if kind == "sentence":
            return [(index, (index,)) for index in range(len(units))]
        groups: list[list[int]] = []
        previous_paragraph: Any = object()
        for unit in units:
            segment = segments.get(unit.segment_ids[0] if unit.segment_ids else "")
            paragraph = getattr(segment, "paragraph", 0)
            if groups and paragraph != previous_paragraph:
                groups.append([])
            if not groups:
                groups.append([])
            groups[-1].append(unit.index)
            previous_paragraph = paragraph
        return [(index, tuple(group)) for index, group in enumerate(groups)]

    segments = [segment for flow in plan.flow for segment in flow.segments]
    if kind == "sentence" and not any(
        {"sentence", "paragraph"}.intersection(getattr(segment, "boundary_after", ()))
        for segment in segments
    ):
        return [(index, (index,)) for index in range(len(units))]
    groups: list[list[int]] = []
    current: list[int] = []
    boundary_kind = "sentence" if kind == "sentence" else "paragraph"
    for unit, segment in zip(units, segments, strict=True):
        current.append(unit.index)
        boundaries = set(getattr(segment, "boundary_after", ()))
        closes_group = boundary_kind in boundaries or (
            kind == "sentence" and "paragraph" in boundaries
        )
        if closes_group:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return [(index, tuple(group)) for index, group in enumerate(groups)]


def _selected_flow_indices(plan: Any, kind: str, start: int, end: int) -> tuple[int, ...]:
    groups = _groups(plan, kind)
    if end > len(groups):
        raise SelectionError(f"{kind} {end} is out of range; plan has {len(groups)} {kind}s")
    selected: list[int] = []
    for _, indices in groups[start - 1 : end]:
        for index in indices:
            if index not in selected:
                selected.append(index)
    if not selected:
        raise SelectionError(f"{kind} selector selected no flows")
    return tuple(selected)


def resolve_unit_selection(plan: Any, selector: str) -> UnitSelection:
    units = _flow_units(plan)
    if not units:
        raise SelectionError("the plan contains no readable flows")
    normalized = (selector or "all").strip().lower()
    if normalized == "all":
        return _with_segments(plan, tuple(unit.index for unit in units), "all")
    if normalized in {"last-paragraph", "last:paragraph"}:
        groups = _groups(plan, "paragraph")
        if not groups:
            raise SelectionError("the plan contains no last paragraph")
        return _with_segments(plan, groups[-1][1], "last-paragraph")
    if ":" not in normalized:
        raise SelectionError(
            "selector must be all, first:N, last:N, paragraph:N[-M], sentence:N[-M], or unit:N[-M]"
        )
    kind, raw = normalized.split(":", 1)
    start, end = _range(raw, kind)
    if kind in {"first", "last", "unit"}:
        if kind == "first":
            selected = units[:end]
        elif kind == "last":
            selected = units[-end:]
        else:
            if end > len(units):
                raise SelectionError(f"unit {end} is out of range; plan has {len(units)} flows")
            selected = units[start - 1 : end]
        if not selected:
            raise SelectionError("selector selected no flows")
        return _with_segments(plan, tuple(unit.index for unit in selected), normalized)
    if kind not in {"paragraph", "sentence"}:
        raise SelectionError(f"unknown selector kind: {kind}")
    return _with_segments(plan, _selected_flow_indices(plan, kind, start, end), normalized)


def resolve_project_selection(
    plan_scopes: tuple[tuple[Any, Any], ...], selector: str
) -> ProjectUnitSelection:
    """Resolve a selector over ordered ``(scope, FlowPlan)`` pairs."""
    if not plan_scopes:
        raise SelectionError("the project contains no plan scopes")
    scopes = tuple((str(getattr(scope, "id", scope)), plan) for scope, plan in plan_scopes)
    entries = [(scope_id, plan, unit) for scope_id, plan in scopes for unit in _flow_units(plan)]
    if not entries:
        raise SelectionError("the project contains no readable flows")
    normalized = (selector or "all").strip().lower()
    if len(scopes) == 1:
        scope_id, plan = scopes[0]
        selection = resolve_unit_selection(plan, normalized)
        return ProjectUnitSelection(
            scopes=(ScopedUnitSelection(scope_id, selection.unit_indices, selection.segment_ids),),
            description=selection.description,
        )

    if normalized == "all":
        selected_entries = entries
    elif normalized in {"last-paragraph", "last:paragraph"}:
        groups = [
            (scope_id, indices)
            for scope_id, plan in scopes
            for _, indices in _groups(plan, "paragraph")
        ]
        if not groups:
            raise SelectionError("the project contains no last paragraph")
        scope_id, indices = groups[-1]
        selected_keys = {(scope_id, index) for index in indices}
        selected_entries = [
            entry for entry in entries if (entry[0], entry[2].index) in selected_keys
        ]
        normalized = "last-paragraph"
    elif ":" not in normalized:
        raise SelectionError(
            "selector must be all, first:N, last:N, paragraph:N[-M], sentence:N[-M], or unit:N[-M]"
        )
    else:
        kind, raw = normalized.split(":", 1)
        start, end = _range(raw, kind)
        if kind in {"first", "last", "unit"}:
            if kind == "first":
                selected_entries = entries[:end]
            elif kind == "last":
                selected_entries = entries[-end:]
            else:
                if end > len(entries):
                    raise SelectionError(
                        f"unit {end} is out of range; project has {len(entries)} flows"
                    )
                selected_entries = entries[start - 1 : end]
        elif kind in {"paragraph", "sentence"}:
            groups = [
                (scope_id, indices)
                for scope_id, plan in scopes
                for _, indices in _groups(plan, kind)
            ]
            if end > len(groups):
                raise SelectionError(
                    f"{kind} {end} is out of range; project has {len(groups)} {kind}s"
                )
            selected_keys = {
                (scope_id, index)
                for scope_id, indices in groups[start - 1 : end]
                for index in indices
            }
            selected_entries = [
                entry for entry in entries if (entry[0], entry[2].index) in selected_keys
            ]
        else:
            raise SelectionError(f"unknown selector kind: {kind}")

    selected_by_scope: dict[str, list[int]] = {scope_id: [] for scope_id, _ in scopes}
    segments_by_scope: dict[str, list[str]] = {scope_id: [] for scope_id, _ in scopes}
    seen_units: dict[str, set[int]] = {scope_id: set() for scope_id, _ in scopes}
    seen_segments: dict[str, set[str]] = {scope_id: set() for scope_id, _ in scopes}
    for scope_id, _plan, unit in selected_entries:
        if unit.index not in seen_units[scope_id]:
            seen_units[scope_id].add(unit.index)
            selected_by_scope[scope_id].append(unit.index)
        for segment_id in unit.segment_ids:
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
    "flow_id",
    "flow_segment_id",
    "resolve_project_selection",
    "resolve_unit_selection",
]
