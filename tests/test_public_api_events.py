from __future__ import annotations

import pytest

from readio.api import ReadioEvent, compose_event_handlers


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
