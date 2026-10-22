"""Strict TOML codec and atomic persistence for Readio render intents."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from importlib import import_module
from pathlib import Path
from typing import Any, Literal, overload

try:
    tomllib = import_module("tomllib")
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    tomllib = import_module("tomli")
from readio.intent import (
    INTENT_SCHEMA,
    ComposeIntent,
    InputIntent,
    OutputIntent,
    PlanningIntent,
    RenderIntent,
    RenderOptionsIntent,
    VoiceIntent,
    _default_bundle_path,
    _default_output_path,
    validate_intent_shape,
)


def _table(value: object, name: str, allowed: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a TOML table")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"unknown key(s) in {name}: {', '.join(str(key) for key in unknown)}")
    return value


@overload
def _string(value: object, name: str) -> str: ...


@overload
def _string(value: object, name: str, *, optional: Literal[False]) -> str: ...


@overload
def _string(value: object, name: str, *, optional: Literal[True]) -> str | None: ...


def _string(value: object, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value


def _bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean")
    return value


def _int(value: object, name: str, *, optional: bool = True) -> int | None:
    if value is None and optional:
        return None
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return value


def _number(value: object, name: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{name} must be a number")
    return float(value)


def _path(value: object, name: str) -> Path | None:
    text = _string(value, name, optional=True)
    return Path(text).expanduser() if text is not None else None


def parse_render_intent(data: Mapping[str, Any]) -> RenderIntent:
    """Parse a mapping into a validated immutable ``readio.render.v1`` value."""
    if not isinstance(data, Mapping):
        raise TypeError("render intent must be a TOML table")
    allowed_root = {"schema", "kind", "input", "voice", "planning", "render", "compose", "output"}
    unknown_root = sorted(set(data) - allowed_root)
    if unknown_root:
        raise ValueError(f"unknown top-level key(s): {', '.join(str(key) for key in unknown_root)}")
    schema = _string(data.get("schema"), "schema")
    if schema != INTENT_SCHEMA:
        raise ValueError(f"unsupported schema {schema!r}; expected {INTENT_SCHEMA!r}")
    kind_value = data.get("kind")
    if kind_value is not None and (
        not isinstance(kind_value, str) or kind_value not in ("book", "document")
    ):
        raise ValueError("kind must be 'book' or 'document'")
    kind = kind_value

    input_data = _table(
        data.get("input", {}), "input", {"source", "bundle", "chapters", "language"}
    )
    voice_data = _table(data.get("voice", {}), "voice", {"profile", "variant"})
    planning_data = _table(
        data.get("planning", {}),
        "planning",
        {
            "diagnostics",
            "text_preparation",
            "overlap_mode",
            "renderability_mode",
            "spacy",
            "spacy_model",
            "spacy_model_size",
            "require_spacy",
        },
    )
    render_data = _table(data.get("render", {}), "render", {"speech", "emphasis_policy"})
    compose_data = _table(
        data.get("compose", {}),
        "compose",
        {
            "emphasis_policy",
            "sample_rate",
            "target_lufs",
            "true_peak_ceiling_dbtp",
            "clip_policy",
            "peak_policy",
        },
    )
    output_data = _table(
        data.get("output", {}),
        "output",
        {"format", "path", "bitrate", "sample_rate", "channels", "cover", "timeline", "metadata"},
    )

    metadata_value = output_data.get("metadata", {})
    if not isinstance(metadata_value, Mapping):
        raise TypeError("output.metadata must be a TOML table")
    metadata = metadata_value
    for key, value in metadata.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(value, str):
            raise ValueError("output.metadata must map non-empty strings to strings")

    bitrate_value = output_data.get("bitrate")
    if bitrate_value is not None and (
        not isinstance(bitrate_value, (str, int)) or isinstance(bitrate_value, bool)
    ):
        raise ValueError("output.bitrate must be a string or integer")

    input_intent = InputIntent(
        source=_path(input_data.get("source"), "input.source"),
        bundle=_path(input_data.get("bundle"), "input.bundle"),
        chapters=_string(input_data.get("chapters"), "input.chapters", optional=True),
        language=_string(input_data.get("language"), "input.language", optional=True),
    )
    voice_intent = VoiceIntent(
        profile=_path(voice_data.get("profile"), "voice.profile"),
        variant=_string(voice_data.get("variant"), "voice.variant", optional=True),
    )
    planning = PlanningIntent(
        diagnostics=_string(planning_data.get("diagnostics", "errors"), "planning.diagnostics"),
        text_preparation=_string(
            planning_data.get("text_preparation", "auto"), "planning.text_preparation"
        ),
        overlap_mode=_string(planning_data.get("overlap_mode", "snap"), "planning.overlap_mode"),
        renderability_mode=_string(
            planning_data.get("renderability_mode", "repair"), "planning.renderability_mode"
        ),
        spacy=_string(planning_data.get("spacy", "auto"), "planning.spacy"),
        spacy_model=_string(
            planning_data.get("spacy_model"), "planning.spacy_model", optional=True
        ),
        spacy_model_size=_string(
            planning_data.get("spacy_model_size"), "planning.spacy_model_size", optional=True
        ),
        require_spacy=_bool(planning_data.get("require_spacy", False), "planning.require_spacy"),
    )
    render = RenderOptionsIntent(
        speech=_string(render_data.get("speech", "all"), "render.speech"),
        emphasis_policy=_string(
            render_data.get("emphasis_policy", "defer-to-compose"), "render.emphasis_policy"
        ),
    )
    compose = ComposeIntent(
        emphasis_policy=_string(
            compose_data.get("emphasis_policy", "segment_fallback"), "compose.emphasis_policy"
        ),
        sample_rate=_int(compose_data.get("sample_rate", 24000), "compose.sample_rate"),
        target_lufs=_number(compose_data.get("target_lufs", -18.0), "compose.target_lufs"),
        true_peak_ceiling_dbtp=_number(
            compose_data.get("true_peak_ceiling_dbtp"), "compose.true_peak_ceiling_dbtp"
        ),
        clip_policy=_string(compose_data.get("clip_policy"), "compose.clip_policy", optional=True),
        peak_policy=_string(compose_data.get("peak_policy"), "compose.peak_policy", optional=True),
    )
    output_format = _string(output_data.get("format", "auto"), "output.format")
    output = OutputIntent(
        format=output_format,
        path=_path(output_data.get("path"), "output.path"),
        bitrate=bitrate_value,
        sample_rate=_int(output_data.get("sample_rate"), "output.sample_rate"),
        channels=_int(output_data.get("channels"), "output.channels"),
        cover=_path(output_data.get("cover"), "output.cover"),
        timeline=_path(output_data.get("timeline"), "output.timeline"),
        metadata=dict(metadata),
    )
    intent = RenderIntent(
        kind=kind,
        input=input_intent,
        voice=voice_intent,
        planning=planning,
        render=render,
        compose=compose,
        output=output,
        schema=schema,
    )
    validate_intent_shape(intent)
    return intent


def load_render_intent(path: str | Path) -> RenderIntent:
    """Load and structurally validate a Readio render-intent TOML file."""
    source = Path(path).expanduser()
    try:
        with source.open("rb") as stream:
            document = tomllib.load(stream)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"invalid render intent TOML in {source}: {error}") from error
    return parse_render_intent(document)


def _toml_value(value: object) -> str:
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    raise TypeError(f"unsupported render-intent TOML value: {type(value).__name__}")


def _table_lines(name: str, values: list[tuple[str, object]]) -> list[str]:
    if not values:
        return []
    lines = [f"[{name}]"]
    lines.extend(f"{key} = {_toml_value(value)}" for key, value in values)
    return lines


def _default_intent_output_path(intent: RenderIntent) -> Path | None:
    source = intent.input.source or intent.input.bundle
    if source is None or intent.kind is None:
        return None
    return _default_output_path(source, intent.kind, intent.output.format)


def _is_default_bundle(intent: RenderIntent) -> bool:
    source = intent.input.source
    bundle = intent.input.bundle
    return source is not None and bundle is not None and bundle == _default_bundle_path(source)


def dump_render_intent(intent: RenderIntent, *, minimal: bool = False) -> str:
    """Serialize an intent deterministically; minimal mode omits effective defaults."""
    validate_intent_shape(intent)
    lines = [f"schema = {_toml_value(intent.schema)}"]
    if intent.kind is not None:
        lines.append(f"kind = {_toml_value(intent.kind)}")

    input_values: list[tuple[str, object]] = []
    if intent.input.source is not None:
        input_values.append(("source", intent.input.source))
    if intent.input.bundle is not None and not (minimal and _is_default_bundle(intent)):
        input_values.append(("bundle", intent.input.bundle))
    if intent.input.chapters is not None and not (minimal and intent.input.chapters == "all"):
        input_values.append(("chapters", intent.input.chapters))
    if intent.input.language is not None:
        input_values.append(("language", intent.input.language))
    lines.extend(_table_lines("input", input_values))

    voice_values: list[tuple[str, object]] = []
    if intent.voice.profile is not None:
        voice_values.append(("profile", intent.voice.profile))
    if intent.voice.variant is not None:
        voice_values.append(("variant", intent.voice.variant))
    lines.extend(_table_lines("voice", voice_values))

    planning_defaults = {
        "diagnostics": "errors",
        "text_preparation": "auto",
        "overlap_mode": "snap",
        "renderability_mode": "repair",
        "spacy": "auto",
        "require_spacy": False,
    }
    planning_values: list[tuple[str, object]] = []
    for name in (
        "diagnostics",
        "text_preparation",
        "overlap_mode",
        "renderability_mode",
        "spacy",
        "spacy_model",
        "spacy_model_size",
        "require_spacy",
    ):
        value = getattr(intent.planning, name)
        if value is not None and not (minimal and planning_defaults.get(name, object()) == value):
            planning_values.append((name, value))
    lines.extend(_table_lines("planning", planning_values))

    render_defaults = {"speech": "all", "emphasis_policy": "defer-to-compose"}
    render_values = [
        (name, getattr(intent.render, name))
        for name in ("speech", "emphasis_policy")
        if not (minimal and getattr(intent.render, name) == render_defaults[name])
    ]
    lines.extend(_table_lines("render", render_values))

    compose_defaults = {
        "emphasis_policy": "segment_fallback",
        "sample_rate": 24000,
        "target_lufs": -18.0,
    }
    compose_values = [
        (name, getattr(intent.compose, name))
        for name in (
            "emphasis_policy",
            "sample_rate",
            "target_lufs",
            "true_peak_ceiling_dbtp",
            "clip_policy",
            "peak_policy",
        )
        if getattr(intent.compose, name) is not None
        and not (minimal and compose_defaults.get(name, object()) == getattr(intent.compose, name))
    ]
    lines.extend(_table_lines("compose", compose_values))

    default_format = {"book": "m4b", "document": "mp3"}.get(intent.kind)
    default_output = _default_intent_output_path(intent)
    output_values: list[tuple[str, object]] = []
    if not (minimal and (intent.output.format == "auto" or intent.output.format == default_format)):
        output_values.append(("format", intent.output.format))
    if intent.output.path is not None and not (minimal and intent.output.path == default_output):
        output_values.append(("path", intent.output.path))
    if intent.output.bitrate is not None and not (
        minimal and intent.kind == "book" and intent.output.bitrate == "96k"
    ):
        output_values.append(("bitrate", intent.output.bitrate))
    for name in ("sample_rate", "channels", "cover", "timeline"):
        value = getattr(intent.output, name)
        if value is not None:
            output_values.append((name, value))
    lines.extend(_table_lines("output", output_values))
    if intent.output.metadata:
        lines.append("[output.metadata]")
        lines.extend(
            f"{json.dumps(key, ensure_ascii=False)} = {_toml_value(value)}"
            for key, value in sorted(intent.output.metadata.items())
        )
    return "\n".join(lines) + "\n"


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def save_render_intent(
    intent: RenderIntent,
    path: str | Path,
    *,
    minimal: bool = False,
    force: bool = False,
) -> Path:
    """Atomically persist an intent, refusing existing targets unless ``force`` is set."""
    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise ValueError(f"refusing to replace a symbolic-link intent: {destination}")
    if destination.exists() and not force:
        raise FileExistsError(f"render intent already exists: {destination}")
    if destination.exists() and not destination.is_file():
        raise ValueError(f"refusing to replace a non-file intent: {destination}")

    content = dump_render_intent(intent, minimal=minimal).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if force:
            os.replace(temporary, destination)
        else:
            os.rename(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return destination


__all__ = [
    "dump_render_intent",
    "load_render_intent",
    "parse_render_intent",
    "save_render_intent",
]
