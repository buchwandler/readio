"""Unified progress and lifecycle events for public Readio operations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from ..jsonutil import JsonValue


@dataclass(frozen=True, slots=True)
class ReadioEvent:
    kind: str
    operation: str
    stage: str | None = None
    message: str | None = None
    completed: int | None = None
    total: int | None = None
    sample_count: int | None = None
    sample_rate: int | None = None
    audio_seconds: float | None = None
    total_audio_seconds: float | None = None
    scope_id: str | None = None
    unit_id: str | None = None
    segment_id: str | None = None
    details: Mapping[str, JsonValue] = field(default_factory=dict)


EventHandler = Callable[[ReadioEvent], None]


def compose_event_handlers(
    application_handler: EventHandler | None,
    operation_handler: EventHandler | None,
) -> EventHandler | None:
    """Call the operation handler first, followed by the application handler."""
    handlers = tuple(
        handler
        for handler in (operation_handler, application_handler)
        if handler is not None
    )
    if not handlers:
        return None

    def dispatch(event: ReadioEvent) -> None:
        for handler in handlers:
            handler(event)

    return dispatch


__all__ = ["EventHandler", "ReadioEvent", "compose_event_handlers"]
