"""Synthesis backend contracts and registry.

This module provides backward compatibility. New code should use
readio.engines instead.
"""

from .base import BackendResolution, DiscoveryInfo, PreparedSynthesisSession, SynthesisBackend
from .registry import backend_ids, default_backend, get_backend, iter_backends

# Re-export new engine types for compatibility
from ..engines.base import EngineAdapter, EngineCapabilities, EngineSelection, EngineSession
from ..engines.catalog import SynthesisTarget
from ..engines.registry import EngineRegistry

__all__ = [
    "BackendResolution",
    "DiscoveryInfo",
    "EngineAdapter",
    "EngineCapabilities",
    "EngineRegistry",
    "EngineSelection",
    "EngineSession",
    "PreparedSynthesisSession",
    "SynthesisBackend",
    "SynthesisTarget",
    "backend_ids",
    "default_backend",
    "get_backend",
    "iter_backends",
]
