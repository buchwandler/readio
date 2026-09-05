"""Shared conversion of Readio values to JSON-safe values."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from pathlib import Path


def json_value(value: object) -> object:
    """Convert common Readio values to values accepted by ``json.dumps``."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Enum):
        return json_value(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return json_value(
            {field.name: getattr(value, field.name) for field in dataclasses.fields(value)}
        )
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [json_value(item) for item in sorted(value, key=str)]
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value
