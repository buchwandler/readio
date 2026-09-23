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
from .discovery import discover_targets
from .registry import (
    CANONICAL_ENGINE_IDS,
    ENGINE_ALIASES,
    EngineRegistry,
    default_engine,
    engine_for_ssmd_provider,
    engine_ids,
    get_engine,
    iter_engines,
    normalize_engine_id,
    ssmd_provider_for_engine,
)

__all__ = [
    "CANONICAL_ENGINE_IDS",
    "ENGINE_ALIASES",
    "EngineAdapter",
    "EngineCapabilities",
    "EngineRegistry",
    "EngineSelection",
    "EngineSession",
    "SynthesisTarget",
    "default_engine",
    "discover_targets",
    "engine_for_ssmd_provider",
    "engine_ids",
    "get_engine",
    "iter_engines",
    "normalize_engine_id",
    "ssmd_provider_for_engine",
]
