"""Engine selection and resolution for Readio.

This module provides types and utilities for resolving engine selections.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .base import EngineSelection

if TYPE_CHECKING:
    from ..plan import RenderPlanV2


@dataclass(frozen=True, slots=True)
class EngineRequest:
    """Request for engine selection."""

    engine: str | None = None
    target_id: str | None = None
    language: str | None = None
    voice: str | None = None
    speaker: str | int | None = None
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EngineResolution:
    """Result of engine selection."""

    selection: EngineSelection
    diagnostics: tuple[Any, ...] = ()


def engine_selection_from_render_plan(render: RenderPlanV2) -> EngineSelection:
    """Convert a resolved render plan into its concrete engine selection."""
    return EngineSelection(
        engine=render.engine,
        target_id=render.target.id,
        language=render.target.language,
        voice=render.target.voice,
        speaker=render.target.speaker,
        options=dict(render.options),
    )


def validate_engine_option(
    engine_id: str,
    option_name: str,
    supported_options: frozenset[str],
) -> bool:
    """Check if an option is supported by the engine."""
    return option_name in supported_options


def reject_incompatible_options(
    engine_id: str,
    options: Mapping[str, Any],
    supported_options: frozenset[str],
) -> list[str]:
    """Return list of unsupported option names."""
    return [name for name in options if name not in supported_options]


__all__ = [
    "EngineRequest",
    "EngineResolution",
    "engine_selection_from_render_plan",
    "reject_incompatible_options",
    "validate_engine_option",
]
