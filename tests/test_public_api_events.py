from __future__ import annotations

from types import SimpleNamespace

import pytest

from readio.api import (
    EventKind,
    EventStage,
    ProgressKind,
    ReadioEvent,
    compose_event_handlers,
)
from readio.api.projects import ProjectService


def test_public_event_vocabulary_is_typed() -> None:
    assert EventKind.__args__ == (
        "operation.started",
        "operation.completed",
        "stage.started",
        "stage.completed",
        "progress",
    )
    assert "composition" in EventStage.__args__
    assert "segment.completed" in ProgressKind.__args__


def test_composed_handlers_call_operation_then_application() -> None:
    calls: list[str] = []
    handler = compose_event_handlers(
        lambda event: calls.append("application"),
        lambda event: calls.append("operation"),
    )
    assert handler is not None

    handler(ReadioEvent(kind="progress", operation="render"))

    assert calls == ["operation", "application"]


def test_handler_exceptions_propagate_without_being_swallowed() -> None:
    error = RuntimeError("stop")
    handler = compose_event_handlers(
        None,
        lambda event: (_ for _ in ()).throw(error),
    )
    assert handler is not None

    with pytest.raises(RuntimeError) as raised:
        handler(ReadioEvent(kind="progress", operation="render"))
    assert raised.value is error


def test_composition_callbacks_translate_to_public_progress() -> None:
    service = object.__new__(ProjectService)
    events: list[ReadioEvent] = []
    handler = service._composition_handler(events.append, "projects.compose")
    assert handler is not None

    handler(
        SimpleNamespace(
            kind="compose_started",
            details={"clip_items": 3, "metadata_kinds": {"speech": 2}},
        )
    )
    handler(
        SimpleNamespace(
            kind="item_started",
            item_kind="clip",
            item_id="internal-item-1",
            item_metadata={"segment_id": "segment-1"},
        )
    )
    handler(
        SimpleNamespace(
            kind="item_completed",
            item_kind="clip",
            item_id="internal-item-1",
            item_metadata={"segment_id": "segment-1"},
        )
    )
    handler(
        SimpleNamespace(
            kind="item_completed",
            item_kind="silence",
            item_id="internal-silence",
            item_metadata={"kind": "silence"},
        )
    )

    assert [(event.kind, event.stage, event.progress_kind) for event in events] == [
        ("progress", "composition", "phase"),
        ("progress", "composition", "segment.started"),
        ("progress", "composition", "segment.completed"),
        ("progress", "composition", "item.completed"),
    ]
    assert (events[0].completed, events[0].total) == (0, 2)
    assert (events[1].completed, events[1].total, events[1].segment_id) == (
        0,
        2,
        "segment-1",
    )
    assert (events[2].completed, events[2].total) == (1, 2)
    assert events[3].completed is None
    assert all(not event.details for event in events)


def test_composition_phase_callbacks_are_progress_events() -> None:
    service = object.__new__(ProjectService)
    events: list[ReadioEvent] = []
    handler = service._phase_handler(events.append, "projects.compose")
    assert handler is not None

    handler("Preparing composition")

    assert events == [
        ReadioEvent(
            kind="progress",
            operation="projects.compose",
            stage="composition",
            progress_kind="phase",
            message="Preparing composition",
        )
    ]
