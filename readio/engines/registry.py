"""Engine registry for Readio multi-engine architecture.

This module provides a registry for discovering and selecting synthesis engines.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from importlib.metadata import PackageNotFoundError, version
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
    "pocket": "pocket",
}

ONNXVOICE_SYSTEM_TO_READIO_ENGINE: dict[str, str] = {
    "kokoro": "pykokoro",
    "piper": "piper",
    "pocket": "pocket",
}
CANONICAL_ENGINE_IDS: frozenset[str] = frozenset({"pykokoro", "piper", "pocket"})


def normalize_engine_id(value: str) -> str:
    """Normalize an engine ID to its canonical form.

    Accepts aliases (e.g. ``pipersynth``) and returns the canonical ID
    (e.g. ``piper``).  Canonical IDs are returned unchanged.
    """
    return ENGINE_ALIASES.get(value, value)


def ssmd_provider_for_engine(engine: str) -> str | None:
    """Return the voice-binding namespace used by an engine for SSMD metadata."""
    return getattr(
        get_engine(normalize_engine_id(engine)).capabilities(), "voice_binding_namespace", None
    )


def engine_for_ssmd_provider(provider: str) -> str:
    """Return the canonical synthesis engine for an SSMD provider."""
    try:
        return ONNXVOICE_SYSTEM_TO_READIO_ENGINE[provider]
    except KeyError as exc:
        raise ValueError(
            f"No synthesis engine is registered for SSMD provider {provider!r}."
        ) from exc


# ---------------------------------------------------------------------------
# EngineRegistry class
# ---------------------------------------------------------------------------


class EngineRegistry:
    """Registry for synthesis engine adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, EngineAdapter] = {}
        self._discover_attempted: set[str] = set()

    def register(self, adapter: EngineAdapter, *, replace: bool = False) -> None:
        """Register an engine adapter, rejecting accidental replacements."""
        existing = self._adapters.get(adapter.id)
        if existing is not None and not replace:
            raise ValueError(f"engine {adapter.id!r} is already registered")
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
        elif engine_id == "pocket":
            self._try_register_pocket()

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

    def _try_register_pocket(self) -> None:
        """Try to register PocketSynth if its adapter is available."""
        try:
            from .pocketsynth import PocketSynthEngineAdapter

            self.register(PocketSynthEngineAdapter())
        except ImportError:
            logger.debug("PocketSynth not available")

    def available_engines(self) -> tuple[str, ...]:
        """Return IDs of all registered engines."""
        return tuple(sorted(self._adapters.keys()))

    def iter_adapters(self) -> Iterator[EngineAdapter]:
        """Iterate over all registered adapters."""
        yield from self._adapters.values()

    def status(self) -> dict[str, dict[str, Any]]:
        """Return installed package and adapter status for known engines."""

        distributions = {
            "pykokoro": "pykokoro",
            "piper": "pipersynth",
            "pocket": "pocketsynth",
        }
        result: dict[str, dict[str, Any]] = {}
        engine_ids = sorted(CANONICAL_ENGINE_IDS | self._adapters.keys())
        for engine_id in engine_ids:
            canonical = normalize_engine_id(engine_id)
            try:
                adapter = self.get(canonical)
            except (ImportError, ValueError):
                adapter = None
            package_name = getattr(adapter, "package_name", distributions.get(canonical, canonical))
            adapter_version = getattr(adapter, "version", None)
            package_version = adapter_version() if callable(adapter_version) else None
            if package_version is None:
                try:
                    package_version = version(package_name)
                except PackageNotFoundError:
                    package_version = None
            compatible = None
            if adapter is not None and package_version is not None:
                compatibility_check = getattr(adapter, "compatible_api", None)
                if compatibility_check is not None:
                    try:
                        compatible = compatibility_check()
                    except (
                        ImportError,
                        SyntaxError,
                        OSError,
                        RuntimeError,
                        AttributeError,
                        TypeError,
                        ValueError,
                    ) as exc:
                        logger.debug("Engine %s API compatibility check failed: %s", canonical, exc)
                        compatible = False
            status = (
                "adapter_unavailable"
                if adapter is None
                else "package_missing"
                if package_version is None
                else "api_incompatible"
                if compatible is False
                else "ready"
            )
            result[canonical] = {
                "adapter": adapter is not None,
                "package": package_version is not None,
                "version": package_version,
                "api_compatible": compatible,
                "status": status,
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


def engine_status() -> dict[str, dict[str, Any]]:
    """Return package and adapter status for all known synthesis engines."""
    return _registry.status()


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
    "engine_for_ssmd_provider",
    "engine_ids",
    "engine_status",
    "get_engine",
    "iter_engines",
    "normalize_engine_id",
    "ssmd_provider_for_engine",
]
