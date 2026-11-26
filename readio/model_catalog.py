"""Normalize VoiceRender model records into Readio-owned public types."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from readio.api.types import EngineModelInfo, ModelState, ModelUpdateState
from readio.errors import CatalogError

_MODEL_STATES = frozenset({"missing", "ready", "outdated", "partial", "corrupt", "unavailable"})


def _value(record: object, key: str, default: object = None) -> object:
    if isinstance(record, Mapping):
        return record.get(key, default)
    return getattr(record, key, default)


def _identifier(record: object, key: str) -> str:
    value = _value(record, key)
    if isinstance(value, str) and value.strip():
        return value
    name = "target" if key == "target_id" else key
    raise CatalogError(
        f"VoiceRender model record has no valid {name} identifier.",
        operation="normalize_model",
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _single_language(value: object) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, (tuple, list)):
        values = tuple(item for item in value if isinstance(item, str) and item)
        return values[0] if len(values) == 1 else None
    return None


def model_state(value: object) -> ModelState:
    """Return only a recognized specialist state consistent with its installed flag."""
    status = _value(value, "status")
    installed = _value(value, "installed")
    if not isinstance(status, str) or type(installed) is not bool:
        return "unknown"
    if status not in _MODEL_STATES:
        return "unknown"
    if status == "missing" and installed:
        return "unknown"
    if status in {"ready", "outdated", "partial", "corrupt"} and not installed:
        return "unknown"
    return cast(ModelState, status)


def model_update_state(value: object) -> ModelUpdateState:
    """VoiceRender 0.2.12 exposes no authoritative update-comparison field."""
    del value
    return "unknown"


def model_info(value: object) -> EngineModelInfo:
    """Normalize fields exposed by VoiceRender's public ModelArtifact contract."""
    engine = _identifier(value, "engine")
    target = _identifier(value, "target_id")
    installed_value = _value(value, "installed")
    installed = installed_value if type(installed_value) is bool else False
    state = model_state(value)
    revision = _optional_string(_value(value, "revision"))
    reason: str | None = None
    if type(installed_value) is not bool:
        reason = "VoiceRender did not report a valid installed flag."
    elif state == "unknown":
        reason = "VoiceRender model status is unknown or inconsistent."
    elif not installed and revision is not None:
        reason = "VoiceRender reported a revision for a model that is not installed."

    return EngineModelInfo(
        engine=engine,
        target=target,
        language=_single_language(_value(value, "languages")),
        label=_optional_string(_value(value, "display_name")),
        installed=installed,
        state=state,
        local_version=revision if installed else None,
        update_state=model_update_state(value),
        reason=reason,
    )


__all__ = ["model_info", "model_state", "model_update_state"]
