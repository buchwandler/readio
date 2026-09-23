"""Supported engine adapter registration contracts."""

from __future__ import annotations

import re

from ..engines.base import (
    EngineAdapter,
    EngineCapabilities,
    EngineSelection,
    EngineSession,
    PreparedSegmentRenderer,
    PreparedUnitRenderer,
    RenderedSegment,
    RenderedUnit,
)
from ..engines.catalog import CatalogRequest, CatalogResult, SynthesisTarget
from ..engines.registry import (
    CANONICAL_ENGINE_IDS,
    _registry,
    engine_ids,
    get_engine,
    normalize_engine_id,
)
from . import errors as api_errors

_ENGINE_ID = re.compile(r"^[a-z][a-z0-9_-]*$")
_REQUIRED_ADAPTER_METHODS = (
    "version",
    "capabilities",
    "discover",
    "resolve",
    "planner_config",
    "canonical_synthesis_identity",
    "open",
)


def register_engine(adapter: EngineAdapter, *, replace: bool = False) -> None:
    """Register an engine adapter for discovery, planning, and rendering."""
    engine_id = getattr(adapter, "id", None)
    if not isinstance(engine_id, str) or not _ENGINE_ID.fullmatch(engine_id):
        raise api_errors.InvalidRequestError(
            "engine id must be a lowercase identifier using letters, digits, '_' or '-'",
            code="engine.invalid_id",
        )
    if normalize_engine_id(engine_id) != engine_id:
        raise api_errors.InvalidRequestError(
            f"engine id {engine_id!r} is an alias, not a canonical identifier",
            code="engine.alias_id",
        )
    if engine_id in CANONICAL_ENGINE_IDS and not replace:
        raise api_errors.InvalidRequestError(
            f"engine id {engine_id!r} is reserved for a built-in adapter",
            code="engine.reserved_id",
        )
    missing = tuple(
        name for name in _REQUIRED_ADAPTER_METHODS if not callable(getattr(adapter, name, None))
    )
    if missing:
        raise api_errors.InvalidRequestError(
            f"engine adapter {engine_id!r} is missing methods: {', '.join(missing)}",
            code="engine.contract_incomplete",
        )
    try:
        capabilities = adapter.capabilities()
    except api_errors.ReadioError:
        raise
    except Exception as error:
        raise api_errors.translate_exception(
            error,
            error_type=api_errors.IntegrationError,
            code="engine.capabilities_failed",
        ) from error
    if not isinstance(capabilities, EngineCapabilities) or capabilities.id != engine_id:
        raise api_errors.InvalidRequestError(
            f"engine adapter {engine_id!r} must return matching EngineCapabilities",
            code="engine.capabilities_invalid",
        )
    existing = _registry.get(engine_id)
    if existing is not None and not replace:
        raise api_errors.InvalidRequestError(
            f"engine {engine_id!r} is already registered; pass replace=True to replace it",
            code="engine.already_registered",
        )
    _registry.register(adapter)



def registered_engines() -> tuple[str, ...]:
    """Return registered engine IDs, lazily discovering the built-in adapters."""
    for engine_id in sorted(CANONICAL_ENGINE_IDS):
        try:
            get_engine(engine_id)
        except (ImportError, ValueError):
            continue
    return engine_ids()


__all__ = [
    "CatalogRequest",
    "CatalogResult",
    "EngineAdapter",
    "EngineCapabilities",
    "EngineSelection",
    "EngineSession",
    "PreparedSegmentRenderer",
    "PreparedUnitRenderer",
    "RenderedSegment",
    "RenderedUnit",
    "SynthesisTarget",
    "register_engine",
    "registered_engines",
]
