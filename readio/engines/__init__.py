"""Engine adapters for Readio multi-engine architecture.

This package provides the engine-neutral contract for synthesis engines.
"""

from .base import (
    EngineAdapter,
    EngineCapabilities,
    EngineSelection,
    EngineSession,
)
from .catalog import SynthesisTarget
from .registry import EngineRegistry

__all__ = [
    "EngineAdapter",
    "EngineCapabilities",
    "EngineRegistry",
    "EngineSelection",
    "EngineSession",
    "SynthesisTarget",
]
