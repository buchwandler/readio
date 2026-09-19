"""Encoding stage for persistent Readio projects."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import soundfile as sf

from ..formats import AudioFormat, ensure_audio_format_available
from ..project import Project, atomic_write_json, canonical_json, hash_file, project_lock
from ..wave import atomic_audio_path, create_audio_sink


def export_id(payload: Mapping[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(payload)).hexdigest()}"


def export_project(project: Project, *, audio_format: AudioFormat = "wav", bitrate: str | None = None, output: Path | None = None) -> dict[str, Any]:
    with project_lock(project, operation="export"):
        ensure_audio_format_available(audio_format)
        master = project.paths["composition_master"]
        if not master.is_file():
            raise ValueError("composition master is missing; run readio compose first")
        master_sha = hash_file(master)
        options = {"bitrate": bitrate} if bitrate is not None else {}
        identity_payload = {"schema": "readio.export.v1", "master_sha256": master_sha, "format": audio_format, "options": options}
        target = output or project.root / "output" / f"{project.manifest.name}.{audio_format}"
        target = Path(target)
        if not target.is_absolute():
            target = project.root / target
        target.parent.mkdir(parents=True, exist_ok=True)
        audio, rate = sf.read(master, always_2d=False, dtype="float32")
        with atomic_audio_path(target, force=True) as temporary, create_audio_sink(temporary, audio_format) as sink:
            sink.write(audio, rate)
        state = {
            "format": "readio.export-state",
            "schema_version": 1,
            "export_id": export_id(identity_payload),
            "master_sha256": master_sha,
            "audio_format": audio_format,
            "options": options,
            "path": target.relative_to(project.root).as_posix() if project.root in target.parents else str(target),
            "output_sha256": hash_file(target),
        }
        atomic_write_json(project.root / "output" / "state.json", state)
        return {"export_id": state["export_id"], "path": target, "format": audio_format, "output_sha256": state["output_sha256"]}


__all__ = ["export_id", "export_project"]
