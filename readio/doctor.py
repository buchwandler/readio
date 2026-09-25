"""Diagnostics and health checks for Readio."""

from __future__ import annotations

import importlib.metadata
from typing import Any

from .engines import engine_status


def check_engine_status() -> dict[str, dict[str, Any]]:
    """Return package and adapter status from the unified engine registry."""
    return engine_status()


def _check_distribution(distribution: str) -> dict[str, Any]:
    try:
        return {"available": True, "version": importlib.metadata.version(distribution)}
    except importlib.metadata.PackageNotFoundError:
        return {"available": False, "version": None}


def check_utterplan() -> dict[str, Any]:
    """Check UtterPlan availability."""
    return _check_distribution("utterplan")


def check_ssmd() -> dict[str, Any]:
    """Check SSMD availability."""
    return _check_distribution("ssmd")


def check_audiocompose() -> dict[str, Any]:
    """Check AudioCompose availability."""
    return _check_distribution("audiocompose")


def check_onnxvoice() -> dict[str, Any]:
    """Check OnnxVoice package availability without initializing a runtime."""
    return _check_distribution("onnxvoice")


def run_doctor() -> str:
    """Return a formatted package and adapter health report."""
    lines = ["Readio Doctor", "=" * 40, "", "Engines:", "-" * 20]
    engines = check_engine_status()
    for engine_id, status in engines.items():
        package = status["version"] or "not installed"
        compatible = status.get("api_compatible")
        api_report = "unknown" if compatible is None else "yes" if compatible else "no"
        lines.extend(
            [
                f"  {engine_id}:",
                f"    adapter: {'yes' if status['adapter'] else 'no'}",
                f"    package: {package}",
                f"    request API compatible: {api_report}",
                f"    status: {status['status']}",
            ]
        )
    lines.append("")

    dependencies = {
        "UtterPlan": check_utterplan(),
        "SSMD": check_ssmd(),
        "AudioCompose": check_audiocompose(),
        "OnnxVoice": check_onnxvoice(),
    }
    lines.extend(["Dependencies:", "-" * 20])
    for name, dependency in dependencies.items():
        version = dependency["version"] or "not installed"
        lines.append(f"  {name}: {version}")
    lines.extend(["", "Recommendations:", "-" * 20])
    for engine_id, status in engines.items():
        if not status["package"]:
            extra = {"pykokoro": "kokoro", "piper": "piper", "pocket": "pocket"}.get(
                engine_id, engine_id
            )
            lines.append(f"  - Install {engine_id}: pip install readio[{extra}]")
    for name, dependency in dependencies.items():
        if not dependency["available"]:
            package = {
                "UtterPlan": "utterplan",
                "SSMD": "ssmd",
                "AudioCompose": "audiocompose",
            }.get(name, name.lower())
            lines.append(f"  - Install {name}: pip install {package}")
    if len(lines) == 10:
        lines.append("  No issues detected.")
    return "\n".join(lines)


__all__ = [
    "check_audiocompose",
    "check_engine_status",
    "check_onnxvoice",
    "check_ssmd",
    "check_utterplan",
    "run_doctor",
]
