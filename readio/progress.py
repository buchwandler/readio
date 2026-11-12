"""In-memory normalization of specialist progress callbacks."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import fields, is_dataclass
from typing import Any

from readio.api.types import PipelineEvent, PipelineStage, WorkflowEvent, WorkflowStage

ProgressHandler = Callable[[PipelineEvent], None] | None


def normalize_progress(
    stage: PipelineStage, handler: ProgressHandler
) -> Callable[[Any], None] | None:
    """Adapt a package event to a common Readio event without persisting it."""

    if handler is None:
        return None

    def forward(source: Any) -> None:
        handler(
            PipelineEvent(
                stage=stage,
                current=_int_attribute(source, "current", "completed"),
                total=_int_attribute(source, "total"),
                message=_message(source),
                source=source,
            )
        )

    return forward


def _int_attribute(value: Any, *names: str) -> int | None:
    for name in names:
        candidate = getattr(value, name, None)
        if isinstance(candidate, int):
            return candidate
    return None


def _message(value: Any) -> str | None:
    for name in ("message", "phase", "kind"):
        candidate = getattr(value, name, None)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


WorkflowProgressHandler = Callable[[WorkflowEvent], None] | None


def normalize_workflow_progress(
    stage: WorkflowStage,
    package: str,
    handler: WorkflowProgressHandler,
    *,
    selector: str | None = None,
) -> Callable[[Any], None] | None:
    """Normalize one specialist callback while retaining its structured event detail."""
    if handler is None:
        return None

    def forward(source: Any) -> None:
        outer = source
        nested = _event_value(outer, "source")
        detail_source = nested if nested is not None else outer

        raw_kind = _event_value(nested, "kind")
        if hasattr(raw_kind, "value"):
            raw_kind = raw_kind.value
        if not isinstance(raw_kind, str) or not raw_kind:
            raw_kind = _event_value(outer, "kind")
            if hasattr(raw_kind, "value"):
                raw_kind = raw_kind.value
        kind = raw_kind if isinstance(raw_kind, str) and raw_kind else "stage.progress"

        message = _event_value(outer, "message")
        if not isinstance(message, str) or not message:
            message = _event_value(nested, "message")
        if not isinstance(message, str) or not message:
            message = _event_value(nested if nested is not None else outer, "phase")

        elapsed = _event_value(nested, "elapsed_seconds")
        if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)):
            elapsed = _event_value(outer, "elapsed_seconds")
        if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)):
            elapsed = None

        current = _event_int(outer, "current", "completed", "completed_units")
        if current is None:
            current = _event_int(nested, "current", "completed", "completed_units")
        total = _event_int(outer, "total", "total_units")
        if total is None:
            total = _event_int(nested, "total", "total_units")

        details = dict(_event_details(detail_source))
        if nested is not None:
            details.update(_event_details(outer))
        details = _canonicalize_chapter_details(details)

        handler(
            WorkflowEvent(
                stage=stage,
                kind=kind,
                package=package,
                selector=selector,
                current=current,
                total=total,
                message=message if isinstance(message, str) else None,
                elapsed_seconds=float(elapsed) if elapsed is not None else None,
                details=details,
            )
        )

    return forward


def _event_value(source: Any, name: str) -> Any:
    return source.get(name) if isinstance(source, Mapping) else getattr(source, name, None)


def _event_int(source: Any, *names: str) -> int | None:
    for name in names:
        value = _event_value(source, name)
        if type(value) is int:
            return value
    return None


def _event_details(source: Any) -> Mapping[str, object]:
    if isinstance(source, Mapping):
        values = source
    else:
        try:
            values = vars(source)
        except TypeError:
            values = (
                {field.name: getattr(source, field.name) for field in fields(source)}
                if is_dataclass(source) and not isinstance(source, type)
                else {}
            )
    reserved = {
        "stage",
        "source",
        "kind",
        "current",
        "completed",
        "completed_units",
        "total",
        "total_units",
        "message",
        "phase",
        "elapsed_seconds",
        "details",
    }
    result = {str(key): value for key, value in values.items() if str(key) not in reserved}
    details = _event_value(source, "details")
    if isinstance(details, Mapping):
        result.update(
            {str(key): value for key, value in details.items() if str(key) not in reserved}
        )
    return result


def _canonicalize_chapter_details(details: Mapping[str, object]) -> dict[str, object]:
    result = dict(details)
    chapter_id = result.get("chapter_id") or result.get("chapter_source_id")
    chapter_scoped = isinstance(chapter_id, str) and bool(chapter_id.strip())
    if chapter_scoped and result.get("chapter_id") != chapter_id:
        result["chapter_id"] = chapter_id

    if chapter_scoped and result.get("chapter_number") is None:
        number = result.get("number")
        if number is not None and not isinstance(number, bool):
            result["chapter_number"] = number

    if chapter_scoped and not result.get("chapter_title"):
        title = result.get("title")
        if isinstance(title, str) and title:
            result["chapter_title"] = title
    return result
