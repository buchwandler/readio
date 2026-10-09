from __future__ import annotations

import math
import os
import tempfile
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

from .engines.registry import normalize_engine_id
from .paths import default_config_path, default_output_dir
from .role_targets import VoiceTarget, voice_target_from_mapping

DEFAULT_KOKORO_ROLES = {
    "narrator": "af_sarah",
    "host": "af_sarah",
    "analyst": "am_michael",
    "guest": "af_bella",
}

G2P_FALLBACKS = ("none", "espeak", "goruut")
LEXICON_DATA_POLICIES = ("auto", "installed-only")
LANGUAGE_DETECTION_MODES = ("off", "auto")

VOICE_LEVEL_MODES = ("off", "calibrated")
SPACY_POLICIES = ("auto", "off", "sm", "md", "lg", "trf")
SPACY_LEGACY_ALIASES = {"required": "sm"}
SHORT_SENTENCE_POLICIES = (
    "off",
    "wrap",
    "phrase",
    "randomized-phrase",
)
DEFAULT_SHORT_SENTENCE_POLICY = "phrase"


@dataclass(frozen=True, slots=True)
class ReaderSettings:
    voice: str | None = None
    lang: str = "en-us"
    speed: float = 1.0
    voice_level: str = "off"
    pause_mode: str = "auto"
    unit: str = "sentence"
    queue_size: int = 2
    device: str | None = None
    engine: str = "kokoro"

    language_detection: str | None = None
    detect_languages: tuple[str, ...] | None = None

    spacy: str = "auto"
    short_sentence: str = DEFAULT_SHORT_SENTENCE_POLICY


@dataclass(frozen=True, slots=True)
class LanguageSettings:
    model: str | None = None
    source: str | None = None
    engine: str | None = None
    quality: str | None = None
    voice: str | None = None
    lexicons: tuple[str, ...] | None = None
    g2p_fallback: str | None = None
    lexicon_data_policy: str | None = None
    allow_experimental: bool = False


@dataclass(frozen=True, slots=True)
class SSMDSettings:
    pass


@dataclass(frozen=True, slots=True)
class PathSettings:
    output: Path = field(default_factory=default_output_dir)


@dataclass(frozen=True, slots=True, eq=False)
class ReadioConfig:
    schema: int = 3
    reader: ReaderSettings = field(default_factory=ReaderSettings)
    ssmd: SSMDSettings = field(default_factory=SSMDSettings)
    paths: PathSettings = field(default_factory=PathSettings)
    languages: Mapping[str, LanguageSettings] = field(default_factory=dict)
    roles: Mapping[str, VoiceTarget] = field(
        default_factory=lambda: {
            role: VoiceTarget("kokoro", voice) for role, voice in DEFAULT_KOKORO_ROLES.items()
        }
    )

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ReadioConfig):
            return (
                self.schema == other.schema
                and self.reader == other.reader
                and self.ssmd == other.ssmd
                and self.paths == other.paths
                and dict(self.languages) == dict(other.languages)
                and dict(self.roles) == dict(other.roles)
            )
        if isinstance(other, ReaderSettings):
            return self.reader == other
        return NotImplemented


_READER_KEYS = tuple(ReaderSettings.__dataclass_fields__)
_SSMD_KEYS = tuple(SSMDSettings.__dataclass_fields__)
_PATH_KEYS = tuple(PathSettings.__dataclass_fields__)
_LANGUAGE_KEYS = tuple(LanguageSettings.__dataclass_fields__)


def config_path() -> Path:
    override = os.environ.get("READIO_CONFIG")
    return Path(override).expanduser() if override else default_config_path()


def default_config() -> ReadioConfig:
    return ReadioConfig()


def normalize_language_key(language: str) -> str:
    """Normalize locale keys in the same way as PyKokoro."""
    if not isinstance(language, str):
        raise TypeError("language must be a string")
    normalized = language.strip().lower().replace("_", "-")
    if not normalized:
        raise ValueError("language must be a non-empty string")
    return normalized


def normalize_spacy_policy(value: object) -> str:
    normalized = str(value).strip().lower()
    normalized = SPACY_LEGACY_ALIASES.get(normalized, normalized)
    if normalized not in SPACY_POLICIES:
        allowed = ", ".join(SPACY_POLICIES)
        raise ValueError(f"reader.spacy must be one of: {allowed}")
    return normalized


def normalize_short_sentence_policy(value: object) -> str:
    normalized = str(value).strip().lower()
    if normalized not in SHORT_SENTENCE_POLICIES:
        allowed = ", ".join(SHORT_SENTENCE_POLICIES)
        raise ValueError(f"reader.short_sentence must be one of: {allowed}")
    return normalized


def normalize_voice_level(value: object) -> str:
    normalized = str(value).strip().lower()
    if normalized not in VOICE_LEVEL_MODES:
        allowed = ", ".join(VOICE_LEVEL_MODES)
        raise ValueError(f"reader.voice_level must be one of: {allowed}")
    return normalized


def _coerce_reader_value(key: str, value: Any) -> Any:
    if key not in _READER_KEYS:
        raise KeyError(f"unknown reader config key {key!r}")
    if key == "speed":
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("speed must be finite and > 0")
        return value
    if key == "voice_level":
        return normalize_voice_level(value)
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
    if key == "spacy":
        return normalize_spacy_policy(value)
    if key == "short_sentence":
        return normalize_short_sentence_policy(value)
    if key == "language_detection":
        return _optional_choice(value, "reader.language_detection", LANGUAGE_DETECTION_MODES)
    if key == "detect_languages":
        if value is None:
            return None
        if isinstance(value, str):
            value = [item.strip() for item in value.split(",") if item.strip()]
        if not isinstance(value, (list, tuple)):
            raise TypeError("reader.detect_languages must be a list")
        languages = tuple(normalize_language_key(item) for item in value)
        if len(languages) != len(set(languages)):
            raise ValueError("reader.detect_languages must not contain duplicates")
        return languages
    if key == "engine":
        if not isinstance(value, str) or not value.strip():
            raise ValueError("reader.engine must be a non-empty engine ID")
        return normalize_engine_id(value)
    if key == "voice":
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("reader.voice must be a non-empty string or None")
        return value.strip()
    return str(value)


def _coerce_ssmd_value(key: str, value: Any) -> Any:
    if key not in _SSMD_KEYS:
        raise KeyError(f"unknown ssmd config key {key!r}")
    if not isinstance(value, bool):
        raise TypeError(f"{key} must be a boolean")
    return value


def _path_value(value: Any, field_name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"paths.{field_name} must be a non-empty string")
    return Path(value).expanduser()


def _warn_legacy_authoring_config(
    ssmd_values: Mapping[str, Any], path_values: Mapping[str, Any]
) -> None:
    removed = [f"paths.{key}" for key in ("templates", "ingest") if key in path_values]
    removed.extend(
        f"ssmd.{key}"
        for key in ("validate_before_render", "fail_on_warn", "roundtrip")
        if key in ssmd_values
    )
    if not removed:
        return
    names = ", ".join(removed)
    warnings.warn(
        "Ignoring deprecated Readio authoring configuration key(s): "
        f"{names}. Readio no longer manages authoring templates, drafts, or "
        "SSMDStudio lint/roundtrip operations; existing directories and files are "
        "not deleted. Back up the config and manually move any authoring assets to "
        "SSMDStudio before removing these entries. Readio's consumer/runtime checks "
        "remain part of planning; ssmd.validate_before_render was never enforced. "
        "New config writes omit these removed keys.",
        UserWarning,
        stacklevel=2,
    )


def _role_targets(values: Any) -> dict[str, VoiceTarget]:
    if values is None:
        return {}
    if not isinstance(values, dict):
        raise TypeError("[roles] must be a TOML table")
    result: dict[str, VoiceTarget] = {}
    for role, value in values.items():
        if not isinstance(role, str) or not role.strip():
            raise ValueError("role names must be non-empty strings")
        result[role] = voice_target_from_mapping(value, name=f"roles.{role}")
    return result


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"languages profile {field_name} must be a non-empty string")
    return value.strip()


def _require_canonical_engine(value: Any, field_name: str) -> None:
    if value is not None and (
        not isinstance(value, str) or not value.strip() or value != normalize_engine_id(value)
    ):
        raise ValueError(
            f"{field_name} must use a canonical engine ID; run `readio config migrate` for v0.3 data"
        )


def _optional_engine(value: Any, field_name: str) -> str | None:
    engine = _optional_string(value, field_name)
    if engine is None:
        return None
    return normalize_engine_id(engine)


def _optional_choice(value: Any, field_name: str, choices: tuple[str, ...]) -> str | None:
    value = _optional_string(value, field_name)
    if value is not None and value not in choices:
        allowed = ", ".join(choices)
        raise ValueError(f"languages profile {field_name} must be one of: {allowed}")
    return value


def _language(value: Any, language: str) -> LanguageSettings:
    if not isinstance(value, dict):
        raise TypeError(f"languages.{language} must be a TOML table")
    lexicons = value.get("lexicons")
    if lexicons is not None:
        if not isinstance(lexicons, (list, tuple)):
            raise TypeError(f"languages.{language}.lexicons must be a list")
        lexicons = tuple(lexicons)
        if any(not isinstance(item, str) or not item.strip() for item in lexicons):
            raise ValueError(f"languages.{language}.lexicons must contain non-empty strings")
        if len(lexicons) != len(set(lexicons)):
            raise ValueError(f"languages.{language}.lexicons must not contain duplicates")
    g2p_fallback = _optional_choice(value.get("g2p_fallback"), "g2p_fallback", G2P_FALLBACKS)
    lexicon_data_policy = _optional_choice(
        value.get("lexicon_data_policy"), "lexicon_data_policy", LEXICON_DATA_POLICIES
    )
    allow_experimental = value.get("allow_experimental", False)
    if not isinstance(allow_experimental, bool):
        raise TypeError(f"languages.{language}.allow_experimental must be a boolean")
    return LanguageSettings(
        engine=_optional_engine(value.get("engine"), f"languages.{language}.engine"),
        model=_optional_string(value.get("model"), "model"),
        source=_optional_string(value.get("source"), "source"),
        quality=_optional_string(value.get("quality"), "quality"),
        voice=_optional_string(value.get("voice"), "voice"),
        lexicons=lexicons,
        g2p_fallback=g2p_fallback,
        lexicon_data_policy=lexicon_data_policy,
        allow_experimental=allow_experimental,
    )


def _languages(values: Any) -> dict[str, LanguageSettings]:
    if values is None:
        return {}
    if not isinstance(values, dict):
        raise TypeError("[languages] must be a TOML table")
    result: dict[str, LanguageSettings] = {}
    for raw_key, value in values.items():
        key = normalize_language_key(raw_key)
        if key in result:
            raise ValueError(f"duplicate normalized language key {key!r}")
        result[key] = _language(value, key)
    return result


def validate_config(cfg: ReadioConfig) -> ReadioConfig:
    if cfg.schema != 3:
        raise ValueError("new Readio configurations must use schema 3")
    _coerce_reader_value("voice", cfg.reader.voice)
    _coerce_reader_value("speed", cfg.reader.speed)
    _coerce_reader_value("voice_level", cfg.reader.voice_level)
    _coerce_reader_value("queue_size", cfg.reader.queue_size)
    _coerce_reader_value("unit", cfg.reader.unit)
    _coerce_reader_value("pause_mode", cfg.reader.pause_mode)
    _coerce_reader_value("engine", cfg.reader.engine)
    _coerce_reader_value("spacy", cfg.reader.spacy)
    _coerce_reader_value("short_sentence", cfg.reader.short_sentence)
    _coerce_reader_value("language_detection", cfg.reader.language_detection)
    _coerce_reader_value("detect_languages", cfg.reader.detect_languages)
    for field_name in _PATH_KEYS:
        value = getattr(cfg.paths, field_name)
        if not isinstance(value, Path) or not str(value):
            raise ValueError(f"paths.{field_name} must be a non-empty path")
    normalized_keys: set[str] = set()
    for language, settings in cfg.languages.items():
        normalized = normalize_language_key(language)
        if language != normalized or normalized in normalized_keys:
            raise ValueError(f"duplicate or non-normalized language key {language!r}")
        normalized_keys.add(normalized)
        _language(
            {
                "model": settings.model,
                "source": settings.source,
                "engine": settings.engine,
                "quality": settings.quality,
                "voice": settings.voice,
                "lexicons": list(settings.lexicons) if settings.lexicons is not None else None,
                "g2p_fallback": settings.g2p_fallback,
                "lexicon_data_policy": settings.lexicon_data_policy,
                "allow_experimental": settings.allow_experimental,
            },
            language,
        )
    for role, target in cfg.roles.items():
        if not role.strip() or not isinstance(target, VoiceTarget):
            raise ValueError("roles must map non-empty role names to VoiceTarget values")
    return cfg


def _reader_from(values: Mapping[str, Any]) -> ReaderSettings:
    updates = {
        key: _coerce_reader_value(key, value)
        for key, value in values.items()
        if key in _READER_KEYS
    }
    return ReaderSettings(**updates)


def _config_from_data(data: Mapping[str, Any]) -> ReadioConfig:
    schema = data.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema != 3:
        raise ValueError(
            f"unsupported Readio config schema {schema!r}; migrate v0.3 data with `readio config migrate`"
        )
    if "voices" in data:
        raise ValueError(
            "provider-specific voice tables are not supported; run `readio config migrate`"
        )

    reader_values = data.get("reader", {})
    if not isinstance(reader_values, dict):
        raise TypeError("[reader] must be a TOML table")
    _require_canonical_engine(reader_values.get("engine", "kokoro"), "reader.engine")
    reader = _reader_from(reader_values)

    ssmd_values = data.get("ssmd", {})
    if not isinstance(ssmd_values, dict):
        raise TypeError("[ssmd] must be a TOML table")
    if "voice_provider" in ssmd_values:
        raise ValueError(
            "ssmd.voice_provider is obsolete; run `readio config migrate` for v0.3 data"
        )
    ssmd = SSMDSettings(
        **{
            key: _coerce_ssmd_value(key, value)
            for key, value in ssmd_values.items()
            if key in _SSMD_KEYS
        }
    )

    path_values = data.get("paths", {})
    if not isinstance(path_values, dict):
        raise TypeError("[paths] must be a TOML table")
    _warn_legacy_authoring_config(ssmd_values, path_values)
    paths = PathSettings(
        **{key: _path_value(value, key) for key, value in path_values.items() if key in _PATH_KEYS}
    )

    language_values = data.get("languages", {})
    if not isinstance(language_values, dict):
        raise TypeError("[languages] must be a TOML table")
    for language, settings in language_values.items():
        if isinstance(settings, Mapping):
            _require_canonical_engine(settings.get("engine"), f"languages.{language}.engine")

    role_values = data.get("roles")
    if role_values is None:
        role_values = {
            role: VoiceTarget("kokoro", voice).to_dict()
            for role, voice in DEFAULT_KOKORO_ROLES.items()
        }
    if not isinstance(role_values, Mapping):
        raise TypeError("[roles] must be a TOML table")
    for role, target in role_values.items():
        if isinstance(target, Mapping):
            _require_canonical_engine(target.get("engine"), f"roles.{role}.engine")

    cfg = ReadioConfig(
        schema=schema,
        reader=reader,
        ssmd=ssmd,
        paths=paths,
        languages=_languages(language_values),
        roles=_role_targets(role_values),
    )
    return validate_config(cfg)


def load_config(path: Path | None = None) -> ReadioConfig:
    path = path or config_path()
    if not path.exists():
        return default_config()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"invalid config at {path}: expected a TOML table")
    return _config_from_data(raw)


def with_overrides(
    cfg: ReadioConfig | ReaderSettings, **values: Any
) -> ReadioConfig | ReaderSettings:
    if isinstance(cfg, ReaderSettings):
        updates = {
            key: _coerce_reader_value(key, value)
            for key, value in values.items()
            if value is not None or key == "voice"
        }
        return ReaderSettings(**{**{key: getattr(cfg, key) for key in _READER_KEYS}, **updates})
    reader_values = {key: getattr(cfg.reader, key) for key in _READER_KEYS}
    for key, value in values.items():
        if value is not None:
            reader_values[key] = _coerce_reader_value(key, value)
    return ReadioConfig(
        schema=cfg.schema,
        reader=ReaderSettings(**reader_values),
        ssmd=cfg.ssmd,
        paths=cfg.paths,
        languages=cfg.languages,
        roles=cfg.roles,
    )


def language_profile(
    cfg: ReadioConfig, language: str
) -> tuple[str | None, LanguageSettings | None]:
    """Resolve an exact locale profile, then its base language profile."""
    normalized = normalize_language_key(language)
    exact = cfg.languages.get(normalized)
    if exact is not None:
        return normalized, exact
    base = normalized.partition("-")[0]
    if base != normalized:
        fallback = cfg.languages.get(base)
        if fallback is not None:
            return base, fallback
    return None, None


def _set_nested(mapping: dict[str, Any], parts: list[str], value: Any) -> None:
    current = mapping
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def _coerce_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "yes", "1", "on"}:
        return True
    if text in {"false", "no", "0", "off"}:
        return False
    raise ValueError(f"{field_name} must be a boolean")


def _serializable_data(cfg: ReadioConfig, *, schema: int = 3) -> dict[str, Any]:
    return {
        "schema": schema,
        "reader": {
            key: _coerce_reader_value(key, getattr(cfg.reader, key))
            for key in _READER_KEYS
            if getattr(cfg.reader, key) is not None
        },
        "paths": {key: str(getattr(cfg.paths, key)) for key in _PATH_KEYS},
        "languages": {
            normalize_language_key(language): {
                key: list(value) if isinstance(value, tuple) else value
                for key, value in {
                    "model": settings.model,
                    "source": settings.source,
                    "engine": normalize_engine_id(settings.engine)
                    if settings.engine is not None
                    else None,
                    "quality": settings.quality,
                    "voice": settings.voice,
                    "lexicons": settings.lexicons,
                    "g2p_fallback": settings.g2p_fallback,
                    "lexicon_data_policy": settings.lexicon_data_policy,
                    "allow_experimental": settings.allow_experimental,
                }.items()
                if value is not None
            }
            for language, settings in cfg.languages.items()
        },
        **(
            {"roles": {role: target.to_dict() for role, target in cfg.roles.items()}}
            if cfg.roles
            else {}
        ),
    }


def set_config_value(
    cfg: ReadioConfig | ReaderSettings, key: str, value: Any
) -> ReadioConfig | ReaderSettings:
    aliases = {
        "voice": "reader.voice",
        "lang": "reader.lang",
        "speed": "reader.speed",
        "voice_level": "reader.voice_level",
    }
    key = aliases.get(key, key)
    if isinstance(cfg, ReaderSettings):
        if "." in key:
            key = key.rsplit(".", 1)[-1]
        if key not in _READER_KEYS:
            raise KeyError(f"unknown config key {key!r}")
        return with_overrides(cfg, **{key: value})

    data = _serializable_data(cfg, schema=3)
    parts = key.split(".")
    if parts[0] == "reader" and len(parts) == 2:
        value = _coerce_reader_value(parts[1], value)
    elif parts[0] == "ssmd" and len(parts) == 2:
        value = _coerce_ssmd_value(parts[1], value)
    elif parts[0] == "paths" and len(parts) == 2:
        value = str(_path_value(value, parts[1]))
    elif len(parts) == 3 and parts[0] == "roles":
        field_name = parts[2]
        if field_name not in {"engine", "voice", "target_id"}:
            raise KeyError(f"unknown role target field {field_name!r}")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"roles.{parts[1]}.{field_name} must be a non-empty string")
        if field_name == "engine":
            value = normalize_engine_id(value)
        _set_nested(data, ["roles", parts[1], field_name], value.strip())
        return _config_from_data(data)
    elif len(parts) == 3 and parts[0] == "languages":
        language = normalize_language_key(parts[1])
        field_name = parts[2]
        if field_name not in _LANGUAGE_KEYS:
            raise KeyError(f"unknown language config key {field_name!r}")
        if field_name == "lexicons":
            value = tuple(item.strip() for item in str(value).split(","))
        elif field_name == "allow_experimental":
            value = _coerce_bool(value, f"languages.{language}.{field_name}")
        elif field_name == "engine":
            value = _optional_engine(value, f"languages.{language}.engine")
        elif field_name in {"model", "source", "quality", "voice"}:
            value = _optional_string(value, field_name)
        elif field_name == "g2p_fallback":
            value = _optional_choice(value, field_name, G2P_FALLBACKS)
        elif field_name == "lexicon_data_policy":
            value = _optional_choice(value, field_name, LEXICON_DATA_POLICIES)
        _set_nested(data, ["languages", language, field_name], value)
        return _config_from_data(data)
    elif key != "schema":
        raise KeyError(f"unknown config key {key!r}")
    else:
        value = int(value)
    _set_nested(data, parts, value)
    return _config_from_data(data)


def dumps_config(cfg: ReadioConfig | ReaderSettings) -> str:
    full_config = ReadioConfig(reader=cfg) if isinstance(cfg, ReaderSettings) else cfg
    validate_config(full_config)
    return tomli_w.dumps(_serializable_data(full_config, schema=3))


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


def role_targets(cfg: ReadioConfig, engine: str | None = None) -> dict[str, VoiceTarget]:
    engine_id = normalize_engine_id(engine) if engine is not None else None
    return dict(
        sorted(
            (role, target)
            for role, target in cfg.roles.items()
            if engine_id is None or target.engine == engine_id
        )
    )


def bind_voice_target(cfg: ReadioConfig, role: str, target: VoiceTarget) -> ReadioConfig:
    """Persist one global engine-qualified role binding."""
    if not role.strip():
        raise ValueError("voice role must be a non-empty string")
    if not isinstance(target, VoiceTarget):
        raise TypeError("target must be a VoiceTarget")
    return ReadioConfig(
        schema=3,
        reader=cfg.reader,
        ssmd=cfg.ssmd,
        paths=cfg.paths,
        languages=cfg.languages,
        roles={**cfg.roles, role: target},
    )


def unbind_voice_target(cfg: ReadioConfig, role: str) -> ReadioConfig:
    """Remove one engine-qualified role binding."""
    if role not in cfg.roles:
        raise ValueError(f"voice role {role!r} is not configured")
    return ReadioConfig(
        schema=3,
        reader=cfg.reader,
        ssmd=cfg.ssmd,
        paths=cfg.paths,
        languages=cfg.languages,
        roles={key: value for key, value in cfg.roles.items() if key != role},
    )
