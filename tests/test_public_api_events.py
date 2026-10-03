from __future__ import annotations

import pytest

from readio.api import (
    EventKind,
    EventStage,
    ProgressKind,
    ReadioEvent,
    compose_event_handlers,
)
from readio.api.projects import ProjectService
from readio.stages.composition import CompositionProgress


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
        CompositionProgress(
            kind="compose_started",
            details={"clip_items": 3, "metadata_kinds": {"speech": 2}},
        )
    )
    handler(
        CompositionProgress(
            kind="item_started",
            item_kind="clip",
            item_id="internal-item-1",
            details={"item_metadata": {"segment_id": "segment-1"}},
        )
    )
    handler(
        CompositionProgress(
            kind="item_completed",
            item_kind="clip",
            item_id="internal-item-1",
            details={"item_metadata": {"segment_id": "segment-1"}},
        )
    )
    handler(
        CompositionProgress(
            kind="item_completed",
            item_kind="silence",
            item_id="internal-silence",
            details={"item_metadata": {"kind": "silence"}},
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


def test_assembly_and_loudness_progress_exposes_finalization_timings() -> None:
    service = object.__new__(ProjectService)
    events: list[ReadioEvent] = []
    handler = service._composition_handler(events.append, "projects.compose")
    assert handler is not None

    handler(CompositionProgress(kind="assembly_started"))
    handler(CompositionProgress(kind="assembly_completed"))
    handler(CompositionProgress(kind="loudness_started"))
    handler(
        CompositionProgress(
            kind="loudness_completed",
            details={
                "analysis_seconds": 0.25,
                "gain_seconds": 0.01,
                "post_gain_metrics_seconds": 0.02,
            },
        )
    )

    assert "Audio assembly complete in" in (events[1].message or "")
    loudness_event = events[3]
    assert loudness_event.message is not None
    assert "analysis 0.250s" in loudness_event.message
    assert "post-gain metrics 0.020s" in loudness_event.message
    assert loudness_event.details["analysis_seconds"] == 0.25
    assert isinstance(loudness_event.details["phase_duration_seconds"], float)


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
