"""Thin facade for VoiceRender's ONNX Runtime controls."""

from __future__ import annotations

from collections.abc import Sequence

import voicerender  # type: ignore[import-not-found]


def runtime_status() -> voicerender.OnnxRuntimeStatus:
    """Return VoiceRender's ONNX Runtime build and provider-policy status."""
    return voicerender.onnx_runtime_status()


def set_runtime_providers(providers: str | Sequence[str]) -> None:
    """Save the default ONNX Runtime providers for future compatible sessions."""
    voicerender.set_onnx_runtime_providers(providers)


def reset_runtime_providers() -> voicerender.OnnxRuntimeStatus:
    """Remove the saved provider policy and return the resulting runtime status."""
    voicerender.clear_onnx_runtime_providers()
    return voicerender.onnx_runtime_status()


__all__ = ["reset_runtime_providers", "runtime_status", "set_runtime_providers"]
