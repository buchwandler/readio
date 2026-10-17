"""Explicit user-authored Readio settings parsing and validation."""

from __future__ import annotations

import math
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


CONFIG_TEMPLATE = """format = "readio-settings"
schema_version = 1

[document]
language = "en-US"

[document.convert]
sequence_fallback_mode = "preserve"

[document.plan]
text_preparation = "auto"
overlap_mode = "snap"
renderability_mode = "repair"
diagnostics = true

[document.plan.linguistics]
use_spacy = true
require_spacy = false
spacy_model_size = "sm"

[render]
emphasis_policy = "defer-to-compose"
force = false
reset_stale_output = false

[compose]
sample_rate = 24000
channels = 1
clip_policy = "clamp"
target_lufs = -18.0
true_peak_ceiling_dbtp = -1.0
peak_policy = "reduce_gain"
collect_metrics = true
emphasis_policy = "segment_fallback"

[export]
format = "m4a"
bitrate = "192k"
channels = 1
"""


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{path} must be a TOML table")
    if not all(isinstance(key, str) for key in value):
        raise ValueError(f"{path} keys must be strings")
    return value


def _keys(table: Mapping[str, Any], allowed: set[str], path: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise ValueError(f"unknown setting(s) in {path}: {', '.join(sorted(unknown))}")


def _string(value: object, path: str, *, choices: set[str] | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    if choices is not None and value not in choices:
        raise ValueError(f"{path} must be one of: {', '.join(sorted(choices))}")
    return value


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{path} must be a boolean")
    return value


def _positive_int(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{path} must be a positive integer")
    return value


def _number(value: object, path: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{path} must be a finite number")
    return value


def validate_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a Readio settings mapping and return a detached plain dictionary."""
    root = _mapping(settings, "settings")
    _keys(root, {"format", "schema_version", "document", "render", "compose", "export"}, "settings")
    if root.get("format") != "readio-settings":
        raise ValueError("format must be 'readio-settings'")
    version = root.get("schema_version")
    if isinstance(version, bool) or version != 1:
        raise ValueError("schema_version must be 1")

    result: dict[str, Any] = {"format": "readio-settings", "schema_version": 1}
    if "document" in root:
        document = _mapping(root["document"], "document")
        _keys(document, {"language", "convert", "plan"}, "document")
        clean_document: dict[str, Any] = {}
        if "language" in document:
            clean_document["language"] = _string(document["language"], "document.language")
        if "convert" in document:
            convert = _mapping(document["convert"], "document.convert")
            _keys(convert, {"sequence_fallback_mode"}, "document.convert")
            clean_document["convert"] = (
                {
                    "sequence_fallback_mode": _string(
                        convert["sequence_fallback_mode"], "document.convert.sequence_fallback_mode"
                    )
                }
                if "sequence_fallback_mode" in convert
                else {}
            )
        if "plan" in document:
            plan = _mapping(document["plan"], "document.plan")
            _keys(
                plan,
                {
                    "text_preparation",
                    "overlap_mode",
                    "renderability_mode",
                    "diagnostics",
                    "linguistics",
                },
                "document.plan",
            )
            clean_plan: dict[str, Any] = {}
            enum_fields = {
                "text_preparation": {"auto", "ssmd", "spokenform", "identity"},
                "overlap_mode": {"snap", "strict"},
                "renderability_mode": {"repair", "strict"},
            }
            for name, values in enum_fields.items():
                if name in plan:
                    clean_plan[name] = _string(plan[name], f"document.plan.{name}", choices=values)
            if "diagnostics" in plan:
                clean_plan["diagnostics"] = _boolean(
                    plan["diagnostics"], "document.plan.diagnostics"
                )
            if "linguistics" in plan:
                linguistics = _mapping(plan["linguistics"], "document.plan.linguistics")
                _keys(
                    linguistics,
                    {"use_spacy", "require_spacy", "spacy_model", "spacy_model_size"},
                    "document.plan.linguistics",
                )
                clean_linguistics: dict[str, Any] = {}
                for name in ("use_spacy", "require_spacy"):
                    if name in linguistics:
                        clean_linguistics[name] = _boolean(
                            linguistics[name], f"document.plan.linguistics.{name}"
                        )
                if "spacy_model" in linguistics:
                    clean_linguistics["spacy_model"] = _string(
                        linguistics["spacy_model"], "document.plan.linguistics.spacy_model"
                    )
                if "spacy_model_size" in linguistics:
                    clean_linguistics["spacy_model_size"] = _string(
                        linguistics["spacy_model_size"],
                        "document.plan.linguistics.spacy_model_size",
                        choices={"sm", "md", "lg", "trf"},
                    )
                clean_plan["linguistics"] = clean_linguistics
            clean_document["plan"] = clean_plan
        result["document"] = clean_document

    if "render" in root:
        render = _mapping(root["render"], "render")
        _keys(render, {"emphasis_policy", "force", "reset_stale_output"}, "render")
        clean_render: dict[str, Any] = {}
        if "emphasis_policy" in render:
            clean_render["emphasis_policy"] = _string(
                render["emphasis_policy"],
                "render.emphasis_policy",
                choices={"reject", "defer-to-compose"},
            )
        for name in ("force", "reset_stale_output"):
            if name in render:
                clean_render[name] = _boolean(render[name], f"render.{name}")
        result["render"] = clean_render

    if "compose" in root:
        compose = _mapping(root["compose"], "compose")
        _keys(
            compose,
            {
                "sample_rate",
                "channels",
                "clip_policy",
                "target_lufs",
                "true_peak_ceiling_dbtp",
                "peak_policy",
                "collect_metrics",
                "emphasis_policy",
            },
            "compose",
        )
        clean_compose: dict[str, Any] = {}
        for name in ("sample_rate", "channels"):
            if name in compose:
                clean_compose[name] = _positive_int(compose[name], f"compose.{name}")
        for name in ("target_lufs", "true_peak_ceiling_dbtp"):
            if name in compose:
                clean_compose[name] = _number(compose[name], f"compose.{name}")
        if "clip_policy" in compose:
            clean_compose["clip_policy"] = _string(
                compose["clip_policy"], "compose.clip_policy", choices={"clamp", "warn", "error"}
            )
        if "peak_policy" in compose:
            clean_compose["peak_policy"] = _string(
                compose["peak_policy"], "compose.peak_policy", choices={"reduce_gain", "error"}
            )
        if "emphasis_policy" in compose:
            clean_compose["emphasis_policy"] = _string(
                compose["emphasis_policy"],
                "compose.emphasis_policy",
                choices={"segment_fallback", "strict"},
            )
        if "collect_metrics" in compose:
            clean_compose["collect_metrics"] = _boolean(
                compose["collect_metrics"], "compose.collect_metrics"
            )
        result["compose"] = clean_compose

    if "export" in root:
        export = _mapping(root["export"], "export")
        _keys(export, {"format", "bitrate", "sample_rate", "channels"}, "export")
        clean_export: dict[str, Any] = {}
        if "format" in export:
            clean_export["format"] = _string(export["format"], "export.format")
        if "bitrate" in export:
            bitrate = export["bitrate"]
            if isinstance(bitrate, bool) or not isinstance(bitrate, (str, int)):
                raise ValueError("export.bitrate must be a string or integer")
            clean_export["bitrate"] = bitrate
        for name in ("sample_rate", "channels"):
            if name in export:
                clean_export[name] = _positive_int(export[name], f"export.{name}")
        result["export"] = clean_export
    return result


def load_settings(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser()
    with source.open("rb") as stream:
        parsed = tomllib.load(stream)
    return validate_settings(parsed)


def merge_settings(settings: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Deep-merge explicit CLI values over settings without mutating either input."""

    def merge(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(left)
        for key, value in right.items():
            current = result.get(key)
            if isinstance(current, Mapping) and isinstance(value, Mapping):
                result[key] = merge(current, value)
            else:
                result[key] = value
        return result

    return merge(settings, overrides)


def init_settings(path: str | Path = "readio.toml", *, force: bool = False) -> Path:
    destination = Path(path).expanduser()
    exists = destination.exists() or destination.is_symlink()
    if exists and not force:
        raise FileExistsError(f"configuration already exists: {destination}")
    if exists and (destination.is_symlink() or not destination.is_file()):
        raise ValueError(f"refusing to replace non-file configuration: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not force:
        with destination.open("x", encoding="utf-8") as stream:
            stream.write(CONFIG_TEMPLATE)
        return destination
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=destination.parent, prefix="readio-settings-", delete=False
    ) as stream:
        stream.write(CONFIG_TEMPLATE)
        temporary = Path(stream.name)
    try:
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


__all__ = [
    "CONFIG_TEMPLATE",
    "init_settings",
    "load_settings",
    "merge_settings",
    "validate_settings",
]
