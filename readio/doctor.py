"""Diagnostics and health checks for Readio.

This module provides the `readio doctor` command that reports
on engine status, dependencies, and configuration.
"""

from __future__ import annotations

import importlib.metadata
import logging
from typing import Any

logger = logging.getLogger(__name__)


def check_engine_status() -> dict[str, dict[str, Any]]:
    """Check status of all known engines.

    Returns:
        Dict mapping engine ID to status info.
    """
    result: dict[str, dict[str, Any]] = {}

    # Check PyKokoro
    result["pykokoro"] = _check_pykokoro()

    # Check PiperSynth
    result["piper"] = _check_piper()

    return result


def _check_pykokoro() -> dict[str, Any]:
    """Check PyKokoro status."""
    status: dict[str, Any] = {
        "adapter": False,
        "package": False,
        "version": None,
        "status": "not_installed",
    }

    # Check if adapter is available
    try:
        from .engines.pykokoro import PyKokoroEngineAdapter

        status["adapter"] = True
    except ImportError:
        pass

    # Check if package is installed
    try:
        version = importlib.metadata.version("pykokoro")
        status["package"] = True
        status["version"] = version
        status["status"] = "ready"
    except importlib.metadata.PackageNotFoundError:
        status["status"] = "package_missing"

    return status


def _check_piper() -> dict[str, Any]:
    """Check PiperSynth status."""
    status: dict[str, Any] = {
        "adapter": False,
        "package": False,
        "version": None,
        "status": "not_installed",
    }

    # Check if adapter is available
    try:
        from .engines.pipersynth import PiperSynthEngineAdapter

        status["adapter"] = True
    except ImportError:
        pass

    # Check if package is installed
    try:
        version = importlib.metadata.version("pipersynth")
        status["package"] = True
        status["version"] = version
        status["status"] = "ready"
    except importlib.metadata.PackageNotFoundError:
        status["status"] = "package_missing"

    return status


def check_utterplan() -> dict[str, Any]:
    """Check UtterPlan availability."""
    try:
        version = importlib.metadata.version("utterplan")
        return {"available": True, "version": version}
    except importlib.metadata.PackageNotFoundError:
        return {"available": False, "version": None}


def check_audiocompose() -> dict[str, Any]:
    """Check AudioCompose availability."""
    try:
        version = importlib.metadata.version("audiocompose")
        return {"available": True, "version": version}
    except importlib.metadata.PackageNotFoundError:
        return {"available": False, "version": None}


def run_doctor() -> str:
    """Run all diagnostics and return a formatted report.

    Returns:
        Formatted diagnostic report.
    """
    lines = ["Readio Doctor", "=" * 40, ""]

    # Engine status
    lines.append("Engines:")
    lines.append("-" * 20)
    engines = check_engine_status()
    for engine_id, status in engines.items():
        adapter = "yes" if status["adapter"] else "no"
        package = status["version"] or "not installed"
        lines.append(f"  {engine_id}:")
        lines.append(f"    adapter: {adapter}")
        lines.append(f"    package: {package}")
        lines.append(f"    status: {status['status']}")
    lines.append("")

    # UtterPlan
    utterplan = check_utterplan()
    lines.append("UtterPlan:")
    lines.append("-" * 20)
    if utterplan["available"]:
        lines.append(f"  version: {utterplan['version']}")
    else:
        lines.append("  not installed")
    lines.append("")

    # AudioCompose
    audiocompose = check_audiocompose()
    lines.append("AudioCompose:")
    lines.append("-" * 20)
    if audiocompose["available"]:
        lines.append(f"  version: {audiocompose['version']}")
    else:
        lines.append("  not installed")
    lines.append("")

    # Recommendations
    lines.append("Recommendations:")
    lines.append("-" * 20)
    if not engines["pykokoro"]["package"]:
        lines.append("  - Install PyKokoro: pip install readio[kokoro]")
    if not engines["piper"]["package"]:
        lines.append("  - Install PiperSynth: pip install readio[piper]")
    if not utterplan["available"]:
        lines.append("  - Install UtterPlan: pip install utterplan")
    if not audiocompose["available"]:
        lines.append("  - Install AudioCompose: pip install audiocompose")

    return "\n".join(lines)


__all__ = ["check_engine_status", "run_doctor"]
