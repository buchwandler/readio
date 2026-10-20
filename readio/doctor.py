"""Dependency health information from public package metadata and APIs."""

from __future__ import annotations

from importlib import metadata
from typing import Any

import voicerender  # type: ignore[import-not-found]

from . import __version__

SPECIALIST_PACKAGES = (
    "ssmdconvert",
    "utterplan",
    "voicerender",
    "audiocompose",
    "audioexport",
)


def doctor_report() -> dict[str, Any]:
    packages: dict[str, str] = {}
    for name in SPECIALIST_PACKAGES:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = "not installed"
    engines = []
    for engine in voicerender.list_engines():
        engine_id = str(engine["id"])
        status = voicerender.engine_status(engine_id)
        engines.append(
            {
                "id": engine_id,
                "installed": bool(engine.get("installed")),
                "version": status.get("distribution_version") or engine.get("installed_version"),
                "status": status.get("status", "unknown"),
                "compatible": status.get("compatible"),
            }
        )
    return {"readio": __version__, "packages": packages, "engines": engines}


def format_doctor_report(report: dict[str, Any] | None = None) -> str:
    report = report or doctor_report()
    lines = [f"readio: {report['readio']}", "Dependencies:"]
    lines.extend(f"  {name}: {version}" for name, version in report["packages"].items())
    lines.append("VoiceRender engines:")
    for engine in report["engines"]:
        version = engine["version"] or "not installed"
        lines.append(f"  {engine['id']}: {version} ({engine['status']})")
        if not engine["installed"]:
            lines.append(f'    Install with: pip install "voicerender[{engine["id"]}]"')
    return "\n".join(lines)


__all__ = ["doctor_report", "format_doctor_report"]
