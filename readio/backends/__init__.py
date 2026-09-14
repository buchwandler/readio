"""Synthesis backend contracts and registry."""

from .base import BackendResolution, DiscoveryInfo, PreparedSynthesisSession, SynthesisBackend
from .registry import backend_ids, default_backend, get_backend, iter_backends

__all__ = [
    "BackendResolution",
    "DiscoveryInfo",
    "PreparedSynthesisSession",
    "SynthesisBackend",
    "backend_ids",
    "default_backend",
    "get_backend",
    "iter_backends",
]
