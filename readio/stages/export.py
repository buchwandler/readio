"""Encoding stage for persistent Readio projects."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import soundfile as sf

from ..formats import AudioFormat, ensure_audio_format_available
from ..project import Project, atomic_write_json, canonical_json, hash_file, project_lock, read_json
from ..wave import atomic_audio_path, create_audio_sink

_M4A_DEFAULT_BITRATE = "192k"
_OPUS_DEFAULT_BITRATE = "96k"
_BITRATE_FORMATS = frozenset({"m4a", "opus"})
_BITRATE_PATTERN = re.compile(r"^(\d+)\s*([kKmM]?)$")
_EXPORT_STATE_PATH = Path("output/state.json")


def export_id(payload: Mapping[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(payload)).hexdigest()}"


def normalize_bitrate(value: str) -> str:
    """Normalize an integer bitrate in bits/s, kbit/s, or Mbit/s to kbit/s."""
    match = _BITRATE_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"invalid bitrate {value!r}; use an integer such as '96k'")
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if amount <= 0:
        raise ValueError("bitrate must be greater than zero")
    if unit == "m":
        amount *= 1000
    elif unit == "":
        amount = max(1, round(amount / 1000))
    return f"{amount}k"


def effective_audio_export_options(
    audio_format: AudioFormat, bitrate: str | None
) -> dict[str, str]:
    if bitrate is not None and audio_format not in _BITRATE_FORMATS:
        raise ValueError(f"bitrate is not supported for {audio_format.upper()} output")
    if audio_format not in _BITRATE_FORMATS:
        return {}
    default = _M4A_DEFAULT_BITRATE if audio_format == "m4a" else _OPUS_DEFAULT_BITRATE
    return {"bitrate": normalize_bitrate(bitrate) if bitrate is not None else default}


def build_audio_export_identity(
    *,
    master_sha256: str,
    audio_format: AudioFormat,
    effective_options: Mapping[str, str],
) -> str:
    return export_id(
        {
            "schema": "readio.export.v2",
            "master_sha256": master_sha256,
            "format": audio_format,
            "options": dict(effective_options),
        }
    )


def target_record(project: Project, target: Path) -> str:
    resolved = target.expanduser().resolve()
    try:
        return resolved.relative_to(project.root.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _load_output_records(project: Project) -> dict[str, dict[str, Any]]:
    state_path = project.root / _EXPORT_STATE_PATH
    if not state_path.is_file():
        return {}
    state = read_json(state_path)
    if state.get("format") == "readio.export-index":
        outputs = state.get("outputs", {})
        if not isinstance(outputs, dict):
            return {}
        return {str(key): value for key, value in outputs.items() if isinstance(value, dict)}
    # Read the previous one-output schema so existing artifacts can be safely
    # recognized and migrated on their next successful export.
    if state.get("format") == "readio.export-state" and isinstance(state.get("path"), str):
        return {state["path"]: state}
    return {}


def project_export_states(project: Project) -> tuple[dict[str, Any], ...]:
    return tuple(_load_output_records(project).values())


def output_state_for(project: Project, target: Path) -> dict[str, Any] | None:
    return _load_output_records(project).get(target_record(project, target))


def store_export_state(project: Project, target: Path, state: Mapping[str, Any]) -> None:
    outputs = _load_output_records(project)
    outputs[target_record(project, target)] = dict(state)
    atomic_write_json(
        project.root / _EXPORT_STATE_PATH,
        {"format": "readio.export-index", "schema_version": 2, "outputs": outputs},
    )


def is_export_current(
    project: Project,
    target: Path,
    *,
    audio_format: AudioFormat,
    bitrate: str | None = None,
) -> bool:
    if not target.is_file():
        return False
    state = output_state_for(project, target)
    if state is None:
        return False
    effective_options = effective_audio_export_options(audio_format, bitrate)
    expected_id = build_audio_export_identity(
        master_sha256=hash_file(project.paths["composition_master"]),
        audio_format=audio_format,
        effective_options=effective_options,
    )
    return (
        state.get("export_id") == expected_id
        and state.get("audio_format") == audio_format
        and state.get("path") == target_record(project, target)
        and state.get("output_sha256") == hash_file(target)
    )


def _can_replace_target(project: Project, target: Path, *, force: bool) -> bool:
    if force or not target.exists():
        return force
    previous = output_state_for(project, target)
    if (
        previous is not None
        and previous.get("path") == target_record(project, target)
        and previous.get("output_sha256") == hash_file(target)
    ):
        return True
    raise FileExistsError(
        f"output already exists and is not an unchanged Readio export: {target}; use --force to replace it"
    )


def export_project(
    project: Project,
    *,
    audio_format: AudioFormat = "wav",
    bitrate: str | None = None,
    output: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    with project_lock(project, operation="export"):
        ensure_audio_format_available(audio_format)
        master = project.paths["composition_master"]
        if not master.is_file():
            raise ValueError("composition master is missing; run readio compose first")
        master_sha = hash_file(master)
        options = effective_audio_export_options(audio_format, bitrate)
        identity = build_audio_export_identity(
            master_sha256=master_sha,
            audio_format=audio_format,
            effective_options=options,
        )
        target = output or project.root / "output" / f"{project.manifest.name}.{audio_format}"
        target = Path(target).expanduser()
        if not target.is_absolute():
            target = project.root / target
        target = target.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        replace_existing = _can_replace_target(project, target, force=force)
        audio, rate = sf.read(master, always_2d=False, dtype="float32")
        with (
            atomic_audio_path(target, force=replace_existing) as temporary,
            create_audio_sink(temporary, audio_format, bitrate=options.get("bitrate")) as sink,
        ):
            sink.write(audio, rate)

        record = target_record(project, target)
        state = {
            "format": "readio.export-state",
            "schema_version": 2,
            "export_id": identity,
            "master_sha256": master_sha,
            "audio_format": audio_format,
            "options": options,
            "path": record,
            "output_sha256": hash_file(target),
        }
        store_export_state(project, target, state)
        return {
            "export_id": state["export_id"],
            "path": target,
            "format": audio_format,
            "output_sha256": state["output_sha256"],
        }


__all__ = [
    "build_audio_export_identity",
    "effective_audio_export_options",
    "export_id",
    "export_project",
    "is_export_current",
    "normalize_bitrate",
    "output_state_for",
    "project_export_states",
    "store_export_state",
    "target_record",
]
