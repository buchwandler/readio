"""Composition of persisted project synthesis artifacts without TTS access."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import soundfile as sf
from audiocompose import (
    AudioClip,
    AudioFileSource,
    AudioJob,
    Composer,
    LoudnessPolicy,
    OutputPolicy,
)

from ..project import (
    Project,
    atomic_write_json,
    canonical_json,
    hash_file,
    project_lock,
    read_json,
)
from .planning import load_scope_plan


def composition_id(payload: Mapping[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(payload)).hexdigest()}"


def _trace_artifacts(project: Project) -> dict[str, Mapping[str, Any]]:
    trace_path = project.paths["synthesis_trace"]
    if not trace_path.is_file():
        raise ValueError("synthesis trace is missing; run readio synth first")
    data = read_json(trace_path)
    if data.get("format") != "readio.synthesis-trace":
        raise ValueError("invalid synthesis trace format")
    return {str(item["unit_id"]): item for item in data.get("units", [])}


def _materialize_part(project: Project, source: Path, part: Path) -> None:
    part.parent.mkdir(parents=True, exist_ok=True)
    temporary = part.with_name(f".{part.name}.tmp")
    temporary.unlink(missing_ok=True)
    try:
        try:
            temporary.hardlink_to(source)
        except (AttributeError, OSError, NotImplementedError):
            temporary.write_bytes(source.read_bytes())
        temporary.replace(part)
    finally:
        temporary.unlink(missing_ok=True)


def build_audio_job(
    project: Project,
    *,
    target_lufs: float | None = None,
    true_peak_ceiling_dbtp: float | None = -1.0,
    peak_policy: str = "reduce_gain",
    clip_policy: str = "clamp",
) -> tuple[AudioJob, dict[str, Any]]:
    plan = load_scope_plan(project)
    trace = _trace_artifacts(project)
    clips: list[AudioClip] = []
    ordered_audio: list[dict[str, Any]] = []
    sample_rate = 24000
    for unit in plan.units:
        item = trace.get(unit.id)
        if item is None or item.get("content_hash") != unit.content_hash:
            raise ValueError(f"synthesis trace has no current audio for {unit.id}")
        source = project.root / str(item["path"])
        expected = str(item.get("audio_sha256", ""))
        if not source.is_file() or not expected or hash_file(source) != expected:
            raise ValueError(f"synthesis audio is missing or corrupt for {unit.id}")
        info = sf.info(source)
        sample_rate = int(info.samplerate)
        part = project.root / "composition" / "parts" / f"{int(unit.index) + 1:06d}.wav"
        _materialize_part(project, source, part)
        clips.append(
            AudioClip(
                id=unit.id,
                source=AudioFileSource(
                    part,
                    expected_sha256=expected,
                    sample_rate=int(info.samplerate),
                    channels=int(info.channels),
                    frames=int(info.frames),
                ),
                metadata={
                    "scope_id": "document",
                    "plan_unit_id": unit.id,
                    "content_hash": unit.content_hash,
                    "synthesis_key": item["synthesis_key"],
                },
            )
        )
        ordered_audio.append({"unit": unit.id, "audio_sha256": expected})
    policy_payload = {
        "sample_rate": sample_rate,
        "channels": 1,
        "loudness": {
            "target_lufs": target_lufs,
            "true_peak_ceiling_dbtp": true_peak_ceiling_dbtp,
            "peak_policy": peak_policy,
        },
        "clip_policy": clip_policy,
    }
    identity_payload = {
        "schema": "readio.composition.v1",
        "ordered_audio": ordered_audio,
        **policy_payload,
    }
    job = AudioJob(
        items=tuple(clips),
        output=OutputPolicy(
            sample_rate=sample_rate,
            channels=1,
            loudness=LoudnessPolicy(target_lufs, true_peak_ceiling_dbtp, peak_policy),
            clip_policy=clip_policy,
        ),
        producer={"readio": "project"},
        source={
            "project_id": project.manifest.project_id,
            "composition_id": composition_id(identity_payload),
        },
    )
    return job, {
        "identity_payload": identity_payload,
        "composition_id": composition_id(identity_payload),
    }


def compose_project(
    project: Project,
    *,
    target_lufs: float | None = None,
    true_peak_ceiling_dbtp: float | None = -1.0,
    peak_policy: str = "reduce_gain",
    clip_policy: str = "clamp",
) -> dict[str, Any]:
    with project_lock(project, operation="compose"):
        job, identity = build_audio_job(
            project,
            target_lufs=target_lufs,
            true_peak_ceiling_dbtp=true_peak_ceiling_dbtp,
            peak_policy=peak_policy,
            clip_policy=clip_policy,
        )
        audiojob_path = project.paths["composition_audiojob"]
        job.save(audiojob_path)
        result = Composer().compose(AudioJob.load(audiojob_path))
        master = project.paths["composition_master"]
        temporary = master.with_name(f".{master.name}.tmp")
        sf.write(temporary, result.audio, result.sample_rate, subtype="PCM_16", format="WAV")
        temporary.replace(master)
        timeline = {
            "format": "readio.composition-timeline",
            "schema_version": 1,
            "sample_rate": result.sample_rate,
            "items": [
                {
                    "id": item.item_id,
                    "kind": item.kind,
                    "start_sample": item.start_sample,
                    "end_sample": item.end_sample,
                    "source_sample_rate": item.source_sample_rate,
                }
                for item in result.items
            ],
            "markers": [
                {
                    "id": marker.id,
                    "sample_offset": marker.sample_offset,
                    "name": marker.name,
                    "item_id": marker.item_id,
                }
                for marker in result.markers
            ],
        }
        atomic_write_json(project.paths["composition_timeline"], timeline)
        master_sha = hash_file(master)
        atomic_write_json(
            project.paths["composition_state"],
            {
                "format": "readio.composition-state",
                "schema_version": 1,
                "composition_id": identity["composition_id"],
                "synthesis_trace_sha256": hash_file(project.paths["synthesis_trace"]),
                "synthesis_profile_id": read_json(project.paths["synthesis_profile"]).get(
                    "profile_id"
                ),
                "audiojob_sha256": hash_file(audiojob_path),
                "master_sha256": master_sha,
                "timeline_sha256": hash_file(project.paths["composition_timeline"]),
                "sample_rate": result.sample_rate,
                "frames": len(result.audio),
                "loudness": (
                    {
                        "integrated_lufs_before": result.loudness.before.integrated_lufs
                        if result.loudness
                        else None,
                        "integrated_lufs_after": result.loudness.after.integrated_lufs
                        if result.loudness
                        else None,
                        "gain_db": result.loudness.applied_gain_db if result.loudness else 0.0,
                    }
                ),
            },
        )
        return {
            "composition_id": identity["composition_id"],
            "master": master,
            "frames": len(result.audio),
            "items": len(result.items),
        }


def compose_artifacts(
    artifacts: Any,
    *,
    target_lufs: float | None = None,
    true_peak_ceiling_dbtp: float | None = -1.0,
    peak_policy: str = "reduce_gain",
    clip_policy: str = "clamp",
    output: Path | None = None,
) -> dict[str, Any]:
    """Compose a temporary/preview selection without changing project state."""
    clips = tuple(
        AudioClip(
            id=artifact.unit_id,
            source=AudioFileSource(
                artifact.cache_path,
                expected_sha256=artifact.audio_sha256,
                sample_rate=artifact.sample_rate,
                channels=artifact.channels,
                frames=artifact.frames,
            ),
            metadata={
                "plan_unit_id": artifact.unit_id,
                "content_hash": artifact.content_hash,
                "synthesis_key": artifact.synthesis_key,
            },
        )
        for artifact in artifacts
    )
    if not clips:
        raise ValueError("preview selected no synthesized units")
    job = AudioJob(
        items=clips,
        output=OutputPolicy(
            sample_rate=clips[0].source.sample_rate or 24000,
            channels=1,
            loudness=LoudnessPolicy(target_lufs, true_peak_ceiling_dbtp, peak_policy),
            clip_policy=clip_policy,
        ),
    )
    result = Composer().compose(job)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        sf.write(output, result.audio, result.sample_rate, subtype="PCM_16", format="WAV")
    return {
        "sample_rate": result.sample_rate,
        "frames": len(result.audio),
        "items": len(result.items),
        "output": output,
    }


__all__ = ["build_audio_job", "compose_artifacts", "compose_project", "composition_id"]
