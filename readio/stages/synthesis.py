"""Incremental, content-addressed project synthesis stage."""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import soundfile as sf

from ..plan import InputRequest, OutputRequest, PlanRequest, SynthesisRequest, resolve_execution_v2
from ..project import Project, atomic_write_json, canonical_json, hash_file, project_lock
from ..selection import resolve_unit_selection
from .planning import load_scope_plan


@dataclass(frozen=True, slots=True)
class SynthesisProfile:
    profile_id: str
    payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "readio.synthesis-profile",
            "schema_version": 1,
            "profile_id": self.profile_id,
            **dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class SynthesisArtifact:
    unit_id: str
    unit_index: int
    content_hash: str
    synthesis_key: str
    path: Path
    cache_path: Path
    audio_sha256: str
    sample_rate: int
    channels: int
    frames: int
    markers: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self, root: Path) -> dict[str, Any]:
        return {
            "scope_id": "document",
            "unit_id": self.unit_id,
            "unit_index": self.unit_index,
            "content_hash": self.content_hash,
            "synthesis_key": self.synthesis_key,
            "path": self.path.relative_to(root).as_posix(),
            "cache_path": self.cache_path.relative_to(root).as_posix(),
            "audio_sha256": self.audio_sha256,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "frames": self.frames,
            "markers": list(self.markers),
        }


def synthesis_profile_id(payload: Mapping[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(payload)).hexdigest()}"


def unit_synthesis_key(content_hash: str, profile_id: str) -> str:
    payload = {
        "schema": "readio.synthesis-unit.v1",
        "content_hash": content_hash,
        "profile_id": profile_id,
    }
    return f"sha256:{hashlib.sha256(canonical_json(payload)).hexdigest()}"


def _safe_key(key: str) -> str:
    return key.replace(":", "-")


def _profile_from_selection(adapter: Any, selection: Any) -> SynthesisProfile:
    payload: dict[str, Any] = {
        "schema": "readio.synthesis-profile.v1",
        "engine": selection.engine,
        "engine_version": adapter.version(),
        "target": {
            "id": selection.target_id,
            "language": selection.language,
            "voice": selection.voice,
            "speaker": selection.speaker,
            "metadata": dict(selection.metadata),
        },
        "rate": selection.options.get("rate", selection.options.get("speed", 1.0)),
        "options": dict(selection.options),
    }
    return SynthesisProfile(synthesis_profile_id(payload), payload)


def _valid_audio(path: Path, expected_sha: str | None = None) -> tuple[int, int, int, str] | None:
    if not path.is_file():
        return None
    try:
        info = sf.info(path)
        digest = hash_file(path)
    except (OSError, RuntimeError, ValueError):
        return None
    if expected_sha is not None and digest != expected_sha:
        return None
    if info.frames <= 0 or info.samplerate <= 0:
        return None
    return int(info.samplerate), int(info.channels), int(info.frames), digest


def _link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{secrets.token_hex(6)}")
    temporary.unlink(missing_ok=True)
    try:
        try:
            os.link(source, temporary)
        except (AttributeError, OSError):
            shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _request_for_project(project: Project, cfg: Any, request: PlanRequest | None) -> PlanRequest:
    if request is not None:
        return request
    reader = cfg.reader
    synthesis = SynthesisRequest(
        language=reader.lang,
        voice=reader.voice,
        engine=reader.engine,
        speed=reader.speed,
        pause_mode=reader.pause_mode,
        unit=reader.unit,
    )
    return PlanRequest(
        operation="render",
        input=InputRequest(document=project.document(), selector="all", source_kind="file"),
        synthesis=synthesis,
        output=OutputRequest(mode="file", requested_format="wav", force=True),
    )


def _resolve_profile(
    project: Project, cfg: Any, request: PlanRequest
) -> tuple[Any, Any, SynthesisProfile]:
    resolved = resolve_execution_v2(cfg, request)
    if not resolved.plan.ok or resolved.selection is None:
        diagnostics = "; ".join(item.message for item in resolved.plan.diagnostics)
        raise ValueError(f"cannot resolve synthesis profile: {diagnostics}")
    from ..engines.registry import get_engine

    adapter = get_engine(resolved.selection.engine)
    return resolved, adapter, _profile_from_selection(adapter, resolved.selection)


def _render_missing(
    project: Project,
    plan: Any,
    adapter: Any,
    selection: Any,
    stale: list[Any],
    profile: SynthesisProfile,
) -> None:
    if not stale:
        return
    cache_dir = project.root / "synthesis" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    with (
        adapter.open(selection) as session,
        session.prepare_plan(plan, options=selection.options) as prepared,
    ):
        for result in prepared.render(indices=tuple(int(unit.index) for unit in stale)):
            index = int(getattr(result, "index", getattr(result, "unit_index", -1)))
            if index < 0:
                metadata = getattr(result, "metadata", {}) or {}
                index = int(metadata.get("unit_index", metadata.get("index", -1)))
            by_index = {int(unit.index): unit for unit in plan.units}
            if index not in by_index:
                raise ValueError(f"engine returned an unknown plan unit index: {index}")
            unit = by_index[index]
            key = unit_synthesis_key(unit.content_hash, profile.profile_id)
            cache_path = cache_dir / f"{_safe_key(key)}.wav"
            temporary = cache_path.with_name(f".tmp-{secrets.token_hex(8)}.wav")
            try:
                sf.write(temporary, result.audio, int(result.sample_rate), subtype="PCM_16")
                checked = _valid_audio(temporary)
                if checked is None:
                    raise ValueError(f"engine produced invalid audio for {unit.id}")
                os.replace(temporary, cache_path)
            finally:
                temporary.unlink(missing_ok=True)
            release = getattr(result, "release_audio", None)
            if release is not None:
                release()


def synthesize_project(
    project: Project,
    cfg: Any,
    *,
    request: PlanRequest | None = None,
    selector: str = "all",
    activate: bool = True,
) -> dict[str, Any]:
    """Synthesize selected stale units, loading the engine only when needed."""
    with project_lock(project, operation="synth"):
        plan = load_scope_plan(project)
        unit_selection = resolve_unit_selection(plan, selector)
        request = _request_for_project(project, cfg, request)
        resolved, adapter, profile = _resolve_profile(project, cfg, request)
        selected = [
            unit for unit in plan.units if int(unit.index) in set(unit_selection.unit_indices)
        ]
        cache_dir = project.root / "synthesis" / "cache"
        stale: list[Any] = []
        cached: dict[int, SynthesisArtifact] = {}
        for unit in selected:
            key = unit_synthesis_key(unit.content_hash, profile.profile_id)
            cache_path = cache_dir / f"{_safe_key(key)}.wav"
            checked = _valid_audio(cache_path)
            if checked is None:
                stale.append(unit)
                continue
            rate, channels, frames, digest = checked
            cached[int(unit.index)] = SynthesisArtifact(
                unit.id,
                int(unit.index),
                unit.content_hash,
                key,
                project.root / "synthesis" / "segments" / f"seg-{int(unit.index) + 1:06d}.wav",
                cache_path,
                digest,
                rate,
                channels,
                frames,
            )
        _render_missing(project, plan, adapter, resolved.selection, stale, profile)
        for unit in stale:
            key = unit_synthesis_key(unit.content_hash, profile.profile_id)
            cache_path = cache_dir / f"{_safe_key(key)}.wav"
            checked = _valid_audio(cache_path)
            if checked is None:
                raise ValueError(f"synthesis did not persist valid audio for {unit.id}")
            rate, channels, frames, digest = checked
            cached[int(unit.index)] = SynthesisArtifact(
                unit.id,
                int(unit.index),
                unit.content_hash,
                key,
                project.root / "synthesis" / "segments" / f"seg-{int(unit.index) + 1:06d}.wav",
                cache_path,
                digest,
                rate,
                channels,
                frames,
            )
        if activate:
            for artifact in cached.values():
                _link_or_copy(artifact.cache_path, artifact.path)
            atomic_write_json(project.paths["synthesis_profile"], profile.to_dict())
            trace = {
                "format": "readio.synthesis-trace",
                "schema_version": 1,
                "profile": {"profile_id": profile.profile_id, **dict(profile.payload)},
                "plans": [
                    {
                        "scope_id": "document",
                        "plan_id": plan.plan_id,
                        "plan_sha256": hash_file(project.root / "plan" / "document.utterplan.json"),
                    }
                ],
                "units": [
                    artifact.to_dict(project.root)
                    for artifact in sorted(cached.values(), key=lambda item: item.unit_index)
                ],
            }
            atomic_write_json(project.paths["synthesis_trace"], trace)
        return {
            "profile": profile,
            "selection": unit_selection,
            "reused": len(selected) - len(stale),
            "rendered": len(stale),
            "artifacts": tuple(cached.values()),
            "activated": activate,
        }


__all__ = [
    "SynthesisArtifact",
    "SynthesisProfile",
    "synthesis_profile_id",
    "synthesize_project",
    "unit_synthesis_key",
]
