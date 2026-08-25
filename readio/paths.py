from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from platformdirs import user_config_path, user_data_path

if TYPE_CHECKING:
    from .config import ReadioConfig
    from .formats import AudioFormat


def default_config_path() -> Path:
    return user_config_path("readio", appauthor=False) / "config.toml"


def default_template_dir() -> Path:
    return user_config_path("readio", appauthor=False) / "templates"


def default_ingest_dir() -> Path:
    return user_data_path("readio", appauthor=False) / "ingest"


def default_output_dir() -> Path:
    return user_data_path("readio", appauthor=False) / "output"


def make_artifact_id(
    now: datetime | None = None,
    random_hex: str | None = None,
) -> str:
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    suffix = random_hex if random_hex is not None else secrets.token_hex(4)
    if len(suffix) != 8 or any(char not in "0123456789abcdef" for char in suffix.lower()):
        raise ValueError("random_hex must be 8 hexadecimal characters")
    return f"{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{suffix.lower()}"


def safe_child(root: Path, name: str) -> Path:
    if not name or "\x00" in name:
        raise ValueError(f"unsafe filename: {name}")
    candidate = Path(name)
    if candidate.is_absolute() or len(candidate.parts) != 1 or candidate.name in {".", ".."}:
        raise ValueError(f"unsafe filename: {name}")
    resolved_root = root.expanduser().resolve()
    resolved_target = (resolved_root / candidate).resolve()
    if resolved_target.parent != resolved_root:
        raise ValueError(f"unsafe filename: {name}")
    return resolved_target


def automatic_ingest_name(*, template: str | None = None, suffix: str = ".txt") -> str:
    prefix = Path(template).stem if template else "readio"
    return f"{prefix}-{make_artifact_id()}{suffix}"


def automatic_render_name(
    input_path: Path | None = None,
    *,
    suffix: str = ".wav",
) -> str:
    if input_path is not None:
        stem = input_path.stem
        if re.search(r"-\d{8}T\d{6}Z-[0-9a-f]{8}$", stem):
            prefix = stem
        else:
            prefix = f"{stem}-{make_artifact_id()}"
    else:
        prefix = f"readio-{make_artifact_id()}"
    return f"{prefix}{suffix}"


def resolve_render_output(
    cfg: ReadioConfig,
    *,
    explicit: Path | None,
    input_path: Path | None,
    audio_format: AudioFormat = "wav",
) -> Path:
    if explicit is not None:
        return explicit.expanduser()
    directory = cfg.paths.output.expanduser()
    suffix = f".{audio_format}"
    candidate = directory / automatic_render_name(input_path, suffix=suffix)
    while candidate.exists():
        if input_path is not None and re.search(r"-\d{8}T\d{6}Z-[0-9a-f]{8}$", input_path.stem):
            candidate = directory / f"{input_path.stem}-{make_artifact_id()}{suffix}"
        else:
            candidate = directory / automatic_render_name(input_path, suffix=suffix)
    return candidate
