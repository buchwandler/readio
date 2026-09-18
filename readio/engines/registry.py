"""Engine registry for Readio multi-engine architecture.

This module provides a registry for discovering and selecting synthesis engines.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import EngineAdapter

logger = logging.getLogger(__name__)


class EngineRegistry:
    """Registry for synthesis engine adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, EngineAdapter] = {}
        self._discover_attempted: set[str] = set()

    def register(self, adapter: EngineAdapter) -> None:
        """Register an engine adapter."""
        self._adapters[adapter.id] = adapter
        logger.debug("Registered engine adapter: %s", adapter.id)

    def get(self, engine_id: str) -> EngineAdapter | None:
        """Get an engine adapter by ID, attempting discovery if not registered."""
        adapter = self._adapters.get(engine_id)
        if adapter is not None:
            return adapter
        if engine_id not in self._discover_attempted:
            self._discover_attempted.add(engine_id)
            self._try_discover(engine_id)
            return self._adapters.get(engine_id)
        return None

    def _try_discover(self, engine_id: str) -> None:
        """Attempt to discover and register an engine by ID."""
        if engine_id == "pykokoro":
            self._try_register_pykokoro()
        elif engine_id == "piper":
            self._try_register_piper()

    def _try_register_pykokoro(self) -> None:
        """Try to register PyKokoro if available."""
        try:
            from .pykokoro import PyKokoroEngineAdapter
            self.register(PyKokoroEngineAdapter())
        except ImportError:
            logger.debug("PyKokoro not available")

    def _try_register_piper(self) -> None:
        """Try to register PiperSynth if available."""
        try:
            from .pipersynth import PiperSynthEngineAdapter
            self.register(PiperSynthEngineAdapter())
        except ImportError:
            logger.debug("PiperSynth not available")

    def available_engines(self) -> tuple[str, ...]:
        """Return IDs of all registered engines."""
        return tuple(sorted(self._adapters.keys()))

    def status(self) -> dict[str, dict[str, Any]]:
        """Return status information for all known engines."""
        result: dict[str, dict[str, Any]] = {}
        for engine_id in sorted(set(list(self._adapters.keys()) + ["pykokoro", "piper"])):
            adapter = self._adapters.get(engine_id)
            if adapter is not None:
                result[engine_id] = {
                    "adapter": True,
                    "version": adapter.version(),
                    "status": "ready",
                }
            else:
                result[engine_id] = {
                    "adapter": False,
                    "version": None,
                    "status": "not_registered",
                }
        return result


__all__ = ["EngineRegistry"]
