"""Explicit registry for installed Readio synthesis backends."""

from __future__ import annotations

from collections.abc import Iterator

from .base import SynthesisBackend
from .pykokoro import PyKokoroBackend

_BACKENDS: dict[str, SynthesisBackend] = {
    "pykokoro": PyKokoroBackend(),
}


def get_backend(name: str) -> SynthesisBackend:
    """Return a registered backend or raise a stable lookup error."""
    try:
        return _BACKENDS[name]
    except KeyError as exc:
        available = ", ".join(_BACKENDS) or "none"
        raise ValueError(f"Unknown synthesis backend {name!r}. Available: {available}") from exc


def iter_backends() -> Iterator[SynthesisBackend]:
    """Iterate registered backends in deterministic registry order."""
    return iter(_BACKENDS.values())


def default_backend() -> SynthesisBackend:
    """Return the built-in backend used by legacy configuration."""
    return get_backend("pykokoro")


def backend_ids() -> tuple[str, ...]:
    """Return registered backend IDs for CLI choices and diagnostics."""
    return tuple(_BACKENDS)


__all__ = ["backend_ids", "default_backend", "get_backend", "iter_backends"]
