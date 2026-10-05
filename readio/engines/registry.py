"""Engine registry for Readio multi-engine architecture.

This module provides a registry for discovering and selecting synthesis engines.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from .api_probe import EngineApiProbe
from .base import EngineAdapter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical engine identity
# ---------------------------------------------------------------------------

ENGINE_ALIASES: dict[str, str] = {
    "pykokoro": "kokoro",
    "pipersynth": "piper",
    "kittensynth": "kitten",
    "supertonicsynth": "supertonic",
}


READIO_ENGINE_TO_ONNXVOICE_SYSTEM: dict[str, str] = {
    "kokoro": "kokoro",
    "piper": "piper",
    "pocket": "pocket",
    "kitten": "kitten",
    "supertonic": "supertonic",
}

ONNXVOICE_SYSTEM_TO_READIO_ENGINE: dict[str, str] = {
    "kokoro": "kokoro",
    "piper": "piper",
    "pocket": "pocket",
    "kitten": "kitten",
    "supertonic": "supertonic",
}
CANONICAL_ENGINE_IDS: frozenset[str] = frozenset(
    {"kokoro", "piper", "pocket", "kitten", "supertonic"}
)


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
    """Return the canonical synthesis engine for an SSMD provider namespace."""
    engine = ONNXVOICE_SYSTEM_TO_READIO_ENGINE.get(provider)
    if engine is not None:
        return engine
    matches = [
        adapter.id
        for adapter in iter_engines()
        if getattr(adapter.capabilities(), "voice_binding_namespace", None) == provider
    ]
    if len(matches) == 1:
        return normalize_engine_id(matches[0])
    if len(matches) > 1:
        raise ValueError(
            f"SSMD provider {provider!r} is exposed by multiple engines: {', '.join(matches)}."
        )
    raise ValueError(f"No synthesis engine is registered for SSMD provider {provider!r}.")


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
        if engine_id == "kokoro":
            self._try_register_pykokoro()
        elif engine_id == "piper":
            self._try_register_piper()
        elif engine_id == "pocket":
            self._try_register_pocket()
        elif engine_id == "kitten":
            self._try_register_kitten()
        elif engine_id == "supertonic":
            self._try_register_supertonic()

    def _try_register_pykokoro(self) -> None:
        """Try to register the Kokoro engine adapter."""
        try:
            from .pykokoro import PyKokoroEngineAdapter

            self.register(PyKokoroEngineAdapter())
        except ImportError:
            logger.debug("Kokoro adapter not available")

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

    def _try_register_kitten(self) -> None:
        """Try to register KittenSynth if available."""
        try:
            from .kittensynth import KittenSynthEngineAdapter

            self.register(KittenSynthEngineAdapter())
        except ImportError:
            logger.debug("KittenSynth not available")

    def _try_register_supertonic(self) -> None:
        """Try to register SupertonicSynth if available."""
        try:
            from .supertonicsynth import SupertonicSynthEngineAdapter

            self.register(SupertonicSynthEngineAdapter())
        except ImportError:
            logger.debug("SupertonicSynth not available")

    def available_engines(self) -> tuple[str, ...]:
        """Return IDs of all registered engines."""
        return tuple(sorted(self._adapters.keys()))

    def iter_adapters(self) -> Iterator[EngineAdapter]:
        """Iterate over all registered adapters."""
        yield from self._adapters.values()

    def status(self) -> dict[str, dict[str, Any]]:
        """Return installed package and structured API-probe status for known engines."""
        distributions = {
            "kokoro": "pykokoro",
            "piper": "pipersynth",
            "pocket": "pocketsynth",
            "kitten": "kittensynth",
            "supertonic": "supertonicsynth",
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
            probe = None
            if adapter is not None:
                probe_method = getattr(adapter, "probe_api", None)
                if callable(probe_method):
                    try:
                        probe = probe_method()
                    except Exception as exc:
                        logger.debug(
                            "Engine %s API probe raised unexpectedly", canonical, exc_info=True
                        )
                        probe = EngineApiProbe(
                            engine=canonical,
                            package=package_name,
                            compatible=False,
                            status="api_probe_failed",
                            failed_stage="adapter_probe",
                            error_type=type(exc).__name__,
                            error_message=str(exc),
                        )
                else:
                    probe = EngineApiProbe(
                        engine=canonical,
                        package=package_name,
                        compatible=False,
                        status="api_probe_failed",
                        failed_stage="adapter_probe",
                        error_type="AttributeError",
                        error_message="Engine adapter does not implement probe_api().",
                    )
            if probe is not None:
                package_version = probe.distribution_version
                package_available = probe.status != "package_missing"
                status = probe.status
                api_compatible = probe.compatible
                module_version = probe.module_version
                module_path = probe.module_path
                request_api_version = probe.api_version
                expected_request_api_version = probe.expected_api_version
                contract_source = probe.contract_source
                missing_symbols = probe.missing_symbols
                missing_methods = probe.missing_methods
                failed_stage = probe.failed_stage
                failed_symbol = probe.failed_symbol
                error_type = probe.error_type
                error_message = probe.error_message
                warnings = probe.warnings
            else:
                try:
                    package_version = version(package_name)
                except PackageNotFoundError:
                    package_version = None
                package_available = package_version is not None
                status = "adapter_unavailable"
                api_compatible = None
                module_version = module_path = None
                request_api_version = expected_request_api_version = contract_source = None
                missing_symbols = missing_methods = warnings = ()
                failed_stage = failed_symbol = error_type = error_message = None
            result[canonical] = {
                "adapter": adapter is not None,
                "package": package_available,
                "version": package_version,
                "module_version": module_version,
                "module_path": module_path,
                "request_api_version": request_api_version,
                "expected_request_api_version": expected_request_api_version,
                "contract_source": contract_source,
                "api_compatible": api_compatible,
                "status": status,
                "missing_symbols": missing_symbols,
                "missing_methods": missing_methods,
                "failed_stage": failed_stage,
                "failed_symbol": failed_symbol,
                "error_type": error_type,
                "error_message": error_message,
                "warnings": warnings,
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
    """Return the default engine adapter (kokoro).

    Raises ``ValueError`` if Kokoro is not available.
    """
    return get_engine("kokoro")


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
