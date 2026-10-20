"""In-memory normalization of specialist progress callbacks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from readio.api.types import PipelineEvent, PipelineStage

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
