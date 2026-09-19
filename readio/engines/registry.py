"""Engine registry for Readio multi-engine architecture.

This module provides a registry for discovering and selecting synthesis engines.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from .base import EngineAdapter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical engine identity
# ---------------------------------------------------------------------------

ENGINE_ALIASES: dict[str, str] = {
    "pipersynth": "piper",
    "kokoro": "pykokoro",
}


READIO_ENGINE_TO_ONNXVOICE_SYSTEM: dict[str, str] = {
    "pykokoro": "kokoro",
    "piper": "piper",
}

ONNXVOICE_SYSTEM_TO_READIO_ENGINE: dict[str, str] = {
    "kokoro": "pykokoro",
    "piper": "piper",
}
CANONICAL_ENGINE_IDS: frozenset[str] = frozenset({"pykokoro", "piper"})


def normalize_engine_id(value: str) -> str:
    """Normalize an engine ID to its canonical form.

    Accepts aliases (e.g. ``pipersynth``) and returns the canonical ID
    (e.g. ``piper``).  Canonical IDs are returned unchanged.
    """
    return ENGINE_ALIASES.get(value, value)


# ---------------------------------------------------------------------------
# EngineRegistry class
# ---------------------------------------------------------------------------


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

    def iter_adapters(self) -> Iterator[EngineAdapter]:
        """Iterate over all registered adapters."""
        yield from self._adapters.values()

    def status(self) -> dict[str, dict[str, Any]]:
        """Return status information for all known engines."""
        result: dict[str, dict[str, Any]] = {}
        for engine_id in sorted(set(list(self._adapters.keys()) + list(CANONICAL_ENGINE_IDS))):
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


# ---------------------------------------------------------------------------
# Module-level registry singleton
# ---------------------------------------------------------------------------

_registry = EngineRegistry()


def get_engine(name: str) -> EngineAdapter:
    """Get an engine adapter by canonical name.

    Normalizes the name first, then looks up the adapter.  Raises
    ``ValueError`` if the engine is not available.
    """
    canonical = normalize_engine_id(name)
    adapter = _registry.get(canonical)
    if adapter is None:
        available = _registry.available_engines()
        raise ValueError(
            f"Engine {name!r} (canonical: {canonical!r}) is not available. "
            f"Available engines: {available}"
        )
    return adapter


def iter_engines() -> Iterator[EngineAdapter]:
    """Iterate over all registered engine adapters."""
    yield from _registry.iter_adapters()


def engine_ids() -> tuple[str, ...]:
    """Return canonical IDs of all registered engines."""
    return _registry.available_engines()


def default_engine() -> EngineAdapter:
    """Return the default engine adapter (pykokoro).

    Raises ``ValueError`` if pykokoro is not available.
    """
    return get_engine("pykokoro")


__all__ = [
    "CANONICAL_ENGINE_IDS",
    "ENGINE_ALIASES",
    "ONNXVOICE_SYSTEM_TO_READIO_ENGINE",
    "READIO_ENGINE_TO_ONNXVOICE_SYSTEM",
    "EngineRegistry",
    "default_engine",
    "engine_ids",
    "get_engine",
    "iter_engines",
    "normalize_engine_id",
]
