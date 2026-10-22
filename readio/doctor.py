"""Dependency health information from public package metadata and APIs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from enum import Enum
from importlib import metadata
from pathlib import Path
from typing import Any

import voicerender  # type: ignore[import-not-found]

from . import __version__
from .runtime import runtime_status

SPECIALIST_PACKAGES = (
    "ssmdconvert",
    "utterplan",
    "voicerender",
    "audiocompose",
    "audioexport",
)


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _runtime_import_error(error: Exception) -> dict[str, Any]:
    message = str(error).strip().splitlines()
    diagnostic = message[0] if message and message[0] else type(error).__name__
    if len(diagnostic) > 300:
        diagnostic = f"{diagnostic[:297]}..."
    return {
        "kind": "onnx",
        "importable": False,
        "distribution_state": "import_failed",
        "import_error": diagnostic,
    }


def _runtime_source_label(source: Any) -> str:
    value = str(source)
    return {
        "default": "built-in default",
        "user_config": "user config",
        "environment": "ONNXVOICE_PROVIDER",
        "environment_variable": "ONNXVOICE_PROVIDER",
    }.get(value, value.replace("_", " "))


def _format_runtime_section(runtime: Mapping[str, Any]) -> list[str]:
    imported = runtime.get("imported_distribution")
    version = runtime.get("module_version")
    distribution = f"{imported} {version}" if imported and version else imported or "not detected"
    available = ", ".join(runtime.get("available_providers") or ()) or "none reported"
    configured = ", ".join(runtime.get("configured_providers") or ()) or "none"
    resolved = ", ".join(runtime.get("resolved_providers") or ()) or "none"
    lines = [
        "ONNX Runtime:",
        f"  distribution: {distribution}",
        f"  state: {runtime.get('distribution_state', 'unknown')}",
        f"  available: {available}",
        f"  configured: {configured}",
        f"  source: {_runtime_source_label(runtime.get('provider_source', 'unknown'))}",
        f"  resolved: {resolved}",
    ]
    for key, label in (("import_error", "diagnostic"), ("resolution_error", "resolution error")):
        if runtime.get(key):
            lines.append(f"  {label}: {runtime[key]}")
    return lines


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
    try:
        runtime_value = _json_value(runtime_status())
        if isinstance(runtime_value, Mapping):
            runtime = {"kind": "onnx", **runtime_value}
        else:
            runtime = {"kind": "onnx", "status": runtime_value}
    except Exception as error:  # noqa: BLE001 - optional runtime probe can fail independently.
        runtime = _runtime_import_error(error)
    return {"readio": __version__, "packages": packages, "engines": engines, "runtime": runtime}


def format_doctor_report(report: dict[str, Any] | None = None) -> str:
    report = report or doctor_report()
    lines = [f"readio: {report['readio']}", "Dependencies:"]
    lines.extend(f"  {name}: {version}" for name, version in report["packages"].items())
    runtime = report.get("runtime")
    if isinstance(runtime, Mapping):
        lines.append("")
        lines.extend(_format_runtime_section(runtime))
    lines.append("VoiceRender engines:")
    for engine in report["engines"]:
        version = engine["version"] or "not installed"
        lines.append(f"  {engine['id']}: {version} ({engine['status']})")
        if not engine["installed"]:
            lines.append(f'    Install with: pip install "voicerender[{engine["id"]}]"')
    return "\n".join(lines)


__all__ = ["doctor_report", "format_doctor_report"]
