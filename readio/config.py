from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

from .paths import default_config_path, default_ingest_dir, default_output_dir, default_template_dir

DEFAULT_KOKORO_VOICES = (
    "af_sarah",
    "am_michael",
    "af_bella",
    "am_adam",
    "bf_emma",
    "bm_george",
)
DEFAULT_KOKORO_ROLES = {
    "narrator": "af_sarah",
    "host": "af_sarah",
    "analyst": "am_michael",
    "guest": "af_bella",
}


@dataclass(frozen=True, slots=True)
class ReaderSettings:
    voice: str = "af_sarah"
    lang: str = "en-us"
    speed: float = 1.0
    pause_mode: str = "tts"
    unit: str = "sentence"
    queue_size: int = 2
    device: str | None = None


ReaderConfig = ReaderSettings


@dataclass(frozen=True, slots=True)
class SSMDSettings:
    voice_provider: str = "kokoro"
    validate_before_render: bool = True
    fail_on_warn: bool = True
    roundtrip: bool = False


@dataclass(frozen=True, slots=True)
class PathSettings:
    templates: Path = field(default_factory=default_template_dir)
    ingest: Path = field(default_factory=default_ingest_dir)
    output: Path = field(default_factory=default_output_dir)


@dataclass(frozen=True, slots=True)
class VoiceProviderSettings:
    ids: tuple[str, ...] = DEFAULT_KOKORO_VOICES
    roles: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_KOKORO_ROLES))


@dataclass(frozen=True, slots=True, eq=False)
class ReadioConfig:
    schema: int = 1
    reader: ReaderSettings = field(default_factory=ReaderSettings)
    ssmd: SSMDSettings = field(default_factory=SSMDSettings)
    paths: PathSettings = field(default_factory=PathSettings)
    voices: Mapping[str, VoiceProviderSettings] = field(
        default_factory=lambda: {"kokoro": VoiceProviderSettings()}
    )

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ReadioConfig):
            return (
                self.schema == other.schema
                and self.reader == other.reader
                and self.ssmd == other.ssmd
                and self.paths == other.paths
                and dict(self.voices) == dict(other.voices)
            )
        if isinstance(other, ReaderSettings):
            return self.reader == other
        return NotImplemented


_READER_KEYS = tuple(ReaderSettings.__dataclass_fields__)
_SSMD_KEYS = tuple(SSMDSettings.__dataclass_fields__)
_PATH_KEYS = tuple(PathSettings.__dataclass_fields__)


def config_path() -> Path:
    override = os.environ.get("READIO_CONFIG")
    return Path(override).expanduser() if override else default_config_path()


def default_config() -> ReadioConfig:
    return ReadioConfig()


def _coerce_reader_value(key: str, value: Any) -> Any:
    if key not in _READER_KEYS:
        raise KeyError(f"unknown reader config key {key!r}")
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
        return None if value in {None, "", "none", "null"} else str(value)
    return str(value)


def _coerce_ssmd_value(key: str, value: Any) -> Any:
    if key not in _SSMD_KEYS:
        raise KeyError(f"unknown ssmd config key {key!r}")
    if key == "voice_provider":
        value = str(value)
        if not value:
            raise ValueError("ssmd.voice_provider must be a non-empty string")
        return value
    if not isinstance(value, bool):
        raise TypeError(f"{key} must be a boolean")
    return value


def _path_value(value: Any, field_name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"paths.{field_name} must be a non-empty string")
    return Path(value).expanduser()


def _provider(value: Any, name: str) -> VoiceProviderSettings:
    if not isinstance(value, dict):
        raise TypeError(f"voices.{name} must be a TOML table")
    ids = value.get("ids", ())
    if not isinstance(ids, list | tuple):
        raise TypeError(f"voices.{name}.ids must be a list")
    if any(not isinstance(item, str) or not item for item in ids):
        raise ValueError(f"voices.{name}.ids must contain non-empty strings")
    normalized_ids = tuple(ids)
    roles_value = value.get("roles", {})
    if not isinstance(roles_value, dict):
        raise TypeError(f"voices.{name}.roles must be a TOML table")
    if any(
        not isinstance(role, str) or not role or not isinstance(target, str) or not target
        for role, target in roles_value.items()
    ):
        raise ValueError(f"voices.{name}.roles must contain non-empty strings")
    roles = dict(roles_value)
    return VoiceProviderSettings(ids=normalized_ids, roles=roles)


def validate_config(cfg: ReadioConfig) -> ReadioConfig:
    _coerce_reader_value("speed", cfg.reader.speed)
    _coerce_reader_value("queue_size", cfg.reader.queue_size)
    _coerce_reader_value("unit", cfg.reader.unit)
    _coerce_reader_value("pause_mode", cfg.reader.pause_mode)
    if not cfg.ssmd.voice_provider:
        raise ValueError("ssmd.voice_provider must be a non-empty string")
    for field_name in _PATH_KEYS:
        value = getattr(cfg.paths, field_name)
        if not isinstance(value, Path) or not str(value):
            raise ValueError(f"paths.{field_name} must be a non-empty path")
    for provider, settings in cfg.voices.items():
        if not provider:
            raise ValueError("voice provider names must be non-empty")
        if len(settings.ids) != len(set(settings.ids)):
            raise ValueError(f"voices.{provider}.ids must not contain duplicates")
        if any(not voice for voice in settings.ids):
            raise ValueError(f"voices.{provider}.ids must contain non-empty strings")
        for role, target in settings.roles.items():
            if not role or not target:
                raise ValueError(f"voices.{provider}.roles must contain non-empty strings")
            if target not in settings.ids:
                raise ValueError(
                    f"configured role {role!r} voice {target!r} is not present in voices.{provider}.ids"
                )
    if cfg.ssmd.voice_provider not in cfg.voices:
        raise ValueError(
            f"selected voice provider {cfg.ssmd.voice_provider!r} has no voices configuration"
        )
    return cfg


def _reader_from(values: Mapping[str, Any]) -> ReaderSettings:
    updates = {
        key: _coerce_reader_value(key, value)
        for key, value in values.items()
        if key in _READER_KEYS
    }
    return ReaderSettings(**updates)


def load_config(path: Path | None = None) -> ReadioConfig:
    path = path or config_path()
    if not path.exists():
        return default_config()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"invalid config at {path}: expected a TOML table")

    reader_values = raw.get("reader", {})
    if not isinstance(reader_values, dict):
        raise TypeError(f"invalid config at {path}: [reader] must be a TOML table")
    if "reader" not in raw:
        reader_values = raw
    reader = _reader_from(reader_values)
    ssmd = SSMDSettings()
    if isinstance(raw.get("ssmd"), dict):
        ssmd = SSMDSettings(
            **{
                key: _coerce_ssmd_value(key, value)
                for key, value in raw["ssmd"].items()
                if key in _SSMD_KEYS
            }
        )
    paths = PathSettings()
    if isinstance(raw.get("paths"), dict):
        paths = PathSettings(
            **{
                key: _path_value(value, key)
                for key, value in raw["paths"].items()
                if key in _PATH_KEYS
            }
        )
    voices: dict[str, VoiceProviderSettings] = {"kokoro": VoiceProviderSettings()}
    if isinstance(raw.get("voices"), dict):
        voices = {name: _provider(value, name) for name, value in raw["voices"].items()}
    cfg = ReadioConfig(
        schema=int(raw.get("schema", 0)),
        reader=reader,
        ssmd=ssmd,
        paths=paths,
        voices=voices,
    )
    return validate_config(cfg)


def with_overrides(
    cfg: ReadioConfig | ReaderSettings, **values: Any
) -> ReadioConfig | ReaderSettings:
    if isinstance(cfg, ReaderSettings):
        updates = {
            key: _coerce_reader_value(key, value)
            for key, value in values.items()
            if value is not None
        }
        return ReaderSettings(**{**{key: getattr(cfg, key) for key in _READER_KEYS}, **updates})
    reader_values = {key: getattr(cfg.reader, key) for key in _READER_KEYS}
    for key, value in values.items():
        if value is not None:
            reader_values[key] = _coerce_reader_value(key, value)
    return ReadioConfig(
        cfg.schema, ReaderSettings(**reader_values), cfg.ssmd, cfg.paths, cfg.voices
    )


def _set_nested(mapping: dict[str, Any], parts: list[str], value: Any) -> None:
    current = mapping
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def set_config_value(
    cfg: ReadioConfig | ReaderSettings, key: str, value: Any
) -> ReadioConfig | ReaderSettings:
    aliases = {"voice": "reader.voice", "lang": "reader.lang", "speed": "reader.speed"}
    key = aliases.get(key, key)
    if isinstance(cfg, ReaderSettings):
        if "." in key:
            key = key.rsplit(".", 1)[-1]
        if key not in _READER_KEYS:
            raise KeyError(f"unknown config key {key!r}")
        return with_overrides(cfg, **{key: value})
    data = {
        "schema": cfg.schema,
        "reader": {key: getattr(cfg.reader, key) for key in _READER_KEYS},
        "ssmd": {key: getattr(cfg.ssmd, key) for key in _SSMD_KEYS},
        "paths": {key: str(getattr(cfg.paths, key)) for key in _PATH_KEYS},
        "voices": {
            provider: {"ids": list(settings.ids), "roles": dict(settings.roles)}
            for provider, settings in cfg.voices.items()
        },
    }
    parts = key.split(".")
    if parts[0] == "reader" and len(parts) == 2:
        value = _coerce_reader_value(parts[1], value)
    elif parts[0] == "ssmd" and len(parts) == 2:
        value = _coerce_ssmd_value(parts[1], value)
    elif parts[0] == "paths" and len(parts) == 2:
        value = str(_path_value(value, parts[1]))
    elif len(parts) == 4 and parts[0] == "voices" and parts[2] == "roles":
        value = str(value)
    elif len(parts) == 3 and parts[0] == "voices" and parts[2] == "ids":
        value = [str(item) for item in str(value).split(",") if item]
    elif key != "schema":
        raise KeyError(f"unknown config key {key!r}")
    else:
        value = int(value)
    _set_nested(data, parts, value)
    return _config_from_data(data)


def _config_from_data(data: Mapping[str, Any]) -> ReadioConfig:
    reader = _reader_from(data.get("reader", {}))
    ssmd_values = data.get("ssmd", {})
    ssmd = SSMDSettings(
        **{key: _coerce_ssmd_value(key, value) for key, value in ssmd_values.items()}
    )
    path_values = data.get("paths", {})
    paths = PathSettings(**{key: _path_value(value, key) for key, value in path_values.items()})
    voices = {name: _provider(value, name) for name, value in data.get("voices", {}).items()}
    return validate_config(
        ReadioConfig(schema=1, reader=reader, ssmd=ssmd, paths=paths, voices=voices)
    )


def dumps_config(cfg: ReadioConfig | ReaderSettings) -> str:
    if isinstance(cfg, ReaderSettings):
        data = {
            "reader": {
                key: getattr(cfg, key) for key in _READER_KEYS if getattr(cfg, key) is not None
            }
        }
    else:
        data = {
            "schema": 1,
            "reader": {
                key: getattr(cfg.reader, key)
                for key in _READER_KEYS
                if getattr(cfg.reader, key) is not None
            },
            "ssmd": {key: getattr(cfg.ssmd, key) for key in _SSMD_KEYS},
            "paths": {key: str(getattr(cfg.paths, key)) for key in _PATH_KEYS},
            "voices": {
                provider: {"ids": list(settings.ids), "roles": dict(settings.roles)}
                for provider, settings in cfg.voices.items()
            },
        }
    return tomli_w.dumps(data)


def save_config(cfg: ReadioConfig | ReaderSettings, path: Path | None = None) -> Path:
    path = (path or config_path()).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temporary:
            temporary.write(dumps_config(cfg))
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    return path


def selected_voice_provider(cfg: ReadioConfig) -> str:
    return cfg.ssmd.voice_provider


def provider_voices(cfg: ReadioConfig, provider: str | None = None) -> tuple[str, ...]:
    name = provider or cfg.ssmd.voice_provider
    try:
        return cfg.voices[name].ids
    except KeyError as exc:
        raise ValueError(f"voice provider {name!r} is not configured") from exc


def voice_role(cfg: ReadioConfig, role: str, provider: str | None = None) -> str:
    name = provider or cfg.ssmd.voice_provider
    try:
        return cfg.voices[name].roles[role]
    except KeyError as exc:
        raise ValueError(f"voice role {role!r} is not configured for provider {name!r}") from exc
