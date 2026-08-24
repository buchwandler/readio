from __future__ import annotations

import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from platformdirs import user_config_path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True, slots=True)
class ReaderConfig:
    voice: str = "af_sarah"
    lang: str = "en-us"
    speed: float = 1.0
    pause_mode: str = "tts"
    unit: str = "sentence"
    queue_size: int = 2
    device: str | None = None


_KEYS = tuple(ReaderConfig.__dataclass_fields__)


def config_path() -> Path:
    override = os.environ.get("READIO_CONFIG")
    if override:
        return Path(override).expanduser()
    return user_config_path("readio", appauthor=False) / "config.toml"


def _coerce_value(key: str, value: Any) -> Any:
    if key not in _KEYS:
        raise KeyError(f"unknown config key {key!r}; expected one of: {', '.join(_KEYS)}")
    if key == "speed":
        value = float(value)
        if value <= 0:
            raise ValueError("speed must be > 0")
        return value
    if key == "queue_size":
        value = int(value)
        if value <= 0:
            raise ValueError("queue_size must be > 0")
        return value
    if key == "unit":
        value = str(value)
        if value not in {"sentence", "paragraph"}:
            raise ValueError("unit must be 'sentence' or 'paragraph'")
        return value
    if key == "pause_mode":
        value = str(value)
        if value not in {"tts", "manual", "auto"}:
            raise ValueError("pause_mode must be 'tts', 'manual', or 'auto'")
        return value
    if key == "device":
        if value in {None, "", "none", "null"}:
            return None
        return str(value)
    return str(value)


def load_config(path: Path | None = None) -> ReaderConfig:
    path = path or config_path()
    if not path.exists():
        return ReaderConfig()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    values = raw.get("reader", raw)
    if not isinstance(values, dict):
        raise TypeError(f"invalid config at {path}: expected a TOML table")
    cfg = ReaderConfig()
    for key, value in values.items():
        if key in _KEYS:
            cfg = replace(cfg, **{key: _coerce_value(key, value)})
    return cfg


def with_overrides(cfg: ReaderConfig, **values: Any) -> ReaderConfig:
    updates: dict[str, Any] = {}
    for key, value in values.items():
        if value is not None:
            updates[key] = _coerce_value(key, value)
    return replace(cfg, **updates)


def set_config_value(cfg: ReaderConfig, key: str, value: Any) -> ReaderConfig:
    return replace(cfg, **{key: _coerce_value(key, value)})


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def dumps_config(cfg: ReaderConfig) -> str:
    data = asdict(cfg)
    lines = ["[reader]"]
    for key in _KEYS:
        value = data[key]
        if value is None:
            continue
        if isinstance(value, str):
            rendered = _toml_string(value)
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = str(value)
        lines.append(f"{key} = {rendered}")
    return "\n".join(lines) + "\n"


def save_config(cfg: ReaderConfig, path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_config(cfg), encoding="utf-8")
    return path
