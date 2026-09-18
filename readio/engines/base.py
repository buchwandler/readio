"""Engine-neutral synthesis contracts for Readio.

This module defines the Protocol classes and dataclasses for the multi-engine
architecture. The contract is designed so that PyKokoro, PiperSynth, and future
engines can implement it without pretending to be each other.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, Protocol

from utterplan import UtterancePlan

from audiocompose import AudioJob


@dataclass(frozen=True, slots=True)
class EngineCapabilities:
    """Capabilities advertised by an engine adapter."""

    id: str
    ssmd_provider: str | None = None
    option_names: frozenset[str] = frozenset()
    supports_prepared_units: bool = True
    supports_audio_job: bool = True
    supports_lexicons: bool = False
    supports_speakers: bool = False
    supports_model_sources: bool = False
    supports_qualities: bool = False


@dataclass(frozen=True, slots=True)
class EngineSelection:
    """Concrete engine selection produced during plan resolution."""

    engine: str
    target_id: str
    language: str
    voice: str | None = None
    speaker: str | int | None = None
    options: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


class EngineSession(Protocol):
    """The rendering part of an engine session consumed by Readio."""

    def prepare_plan(
        self,
        plan: UtterancePlan,
        *,
        options: Mapping[str, Any],
    ) -> AbstractContextManager[Any]:
        """Prepare renderer units from an existing UtterancePlan."""
        ...

    def to_audio_job(
        self,
        plan: UtterancePlan,
        *,
        options: Mapping[str, Any],
    ) -> AudioJob:
        """Create an AudioJob from an existing UtterancePlan."""
        ...


class EngineAdapter(Protocol):
    """Adapter boundary for one Readio synthesis engine.

    This is the main interface that each engine must implement.
    It combines catalog/resolution, planning profile, and rendering.
    """

    id: str

    def version(self) -> str | None:
        """Return the installed engine package version, or None."""
        ...

    def capabilities(self) -> EngineCapabilities:
        """Return the capabilities of this engine."""
        ...

    def discover(
        self,
        request: Any,
    ) -> Any:
        """Discover available targets for this engine."""
        ...

    def resolve(
        self,
        request: Any,
    ) -> tuple[EngineSelection, tuple[Any, ...]]:
        """Resolve a concrete selection from a request."""
        ...

    def planner_config(
        self,
        selection: EngineSelection,
        planning: Any,
    ) -> Any:
        """Return the planner configuration needed for this selection."""
        ...

    def open(
        self,
        selection: EngineSelection,
    ) -> AbstractContextManager[EngineSession]:
        """Open a rendering session for the given selection."""
        ...


__all__ = [
    "EngineAdapter",
    "EngineCapabilities",
    "EngineSelection",
    "EngineSession",
]
