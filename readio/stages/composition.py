"""Composition of canonical project speech artifacts without TTS access."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from audiocompose import (
    AudioBufferSource,
    AudioClip,
    AudioFileSource,
    AudioJob,
    Composer,
    CompositionProgressCallback,
    FadeIn,
    FadeOut,
    Gain,
    LoudnessPolicy,
    OutputPolicy,
    PitchShift,
    Tempo,
)

from ..project import Project, atomic_write_json, canonical_json, hash_file, project_lock, read_json
from ..stages.speech_identity import segment_speech_hash, segment_synthesis_key
from .planning import load_scope_plan
from .synthesis import _valid_audio


def composition_id(payload: Mapping[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(payload)).hexdigest()}"


def seconds_to_frames(seconds: float, sample_rate: int) -> int:
    """Use the one composition rounding rule for layout and silence audio."""
    return round(float(seconds) * sample_rate)


def _numeric(value: Any, values: Mapping[str, float], default: float) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return float(values.get(str(value).strip().lower(), default))


def _speech_operations(segment: Any, composition: Mapping[str, Any] | None = None) -> tuple[Any, ...]:
    composition = composition or {}
    directives = getattr(segment, "directives", None)
    prosody = getattr(directives, "prosody", None)
    operations: list[Any] = []
    rate_value = getattr(prosody, "rate", None)
    if rate_value is None:
        rate_value = composition.get("rate", composition.get("speed"))
    rate = _numeric(
        rate_value,
        {"x-slow": 0.65, "slow": 0.8, "medium": 1.0, "fast": 1.25, "x-fast": 1.5},
        1.0,
    )
    if rate != 1.0:
        operations.append(Tempo(rate))
    pitch_value = getattr(prosody, "pitch", None)
    if pitch_value is None:
        pitch_value = composition.get("pitch")
    pitch = _numeric(
        pitch_value,
        {"x-low": -4.0, "low": -2.0, "medium": 0.0, "high": 2.0, "x-high": 4.0},
        0.0,
    )
    if pitch:
        operations.append(PitchShift(pitch))
    volume_value = getattr(prosody, "volume", None)
    if volume_value is None:
        volume_value = composition.get("volume")
    volume = _numeric(
        volume_value,
        {"silent": -60.0, "x-soft": -12.0, "soft": -6.0, "medium": 0.0, "loud": 6.0, "x-loud": 12.0},
        0.0,
    )
    if volume:
        operations.append(Gain(volume))
    emphasis = getattr(directives, "emphasis", None)
    emphasis_value = getattr(emphasis, "level", None)
    if emphasis_value is None:
        emphasis_value = composition.get("emphasis")
    emphasis_gain = _numeric(
        emphasis_value,
        {"reduced": -2.0, "moderate": 2.0, "strong": 4.0},
        0.0,
    )
    if emphasis_gain:
        operations.append(Gain(emphasis_gain))
    audio = getattr(directives, "audio", None)
    fade_in = getattr(audio, "fade_in", None)
    fade_out = getattr(audio, "fade_out", None)
    if fade_in is not None:
        operations.append(FadeIn(_numeric(fade_in, {}, 0.0)))
    if fade_out is not None:
        operations.append(FadeOut(_numeric(fade_out, {}, 0.0)))
    return tuple(operations)


def _operation_payload(operations: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [dict(operation.to_dict()) for operation in operations]


def _pause_seconds(pause: Any) -> tuple[float, tuple[str, ...]]:
    if pause is None:
        return 0.0, ()
    return float(getattr(pause, "seconds", 0.0)), tuple(getattr(pause, "events", ()) or ())


def _cache_entries(project: Project, plan: Any) -> dict[str, dict[str, Any]]:
    profile = read_json(project.paths["synthesis_profile"])
    canonical = profile.get("canonical")
    profile_id = str(profile.get("profile_id", ""))
    if not isinstance(canonical, dict) or not profile_id:
        raise ValueError("synthesis profile is missing canonical identity")
    entries: dict[str, dict[str, Any]] = {}
    cache_dir = project.root / "synthesis" / "cache"
    for segment in plan.segments:
        speech_hash = segment_speech_hash(plan, segment, canonical)
        key = segment_synthesis_key(speech_hash, profile_id)
        cache_path = cache_dir / f"{key.replace(':', '-')}.wav"
        sidecar_path = cache_dir / f"{key.replace(':', '-')}.json"
        checked = _valid_audio(cache_path)
        if checked is None:
            raise ValueError(f"synthesis audio is missing or corrupt for {segment.id}")
        try:
            sidecar = read_json(sidecar_path)
        except (OSError, ValueError) as exc:
            raise ValueError(f"synthesis sidecar is missing or corrupt for {segment.id}") from exc
        if (sidecar.get("speech_hash") != speech_hash or sidecar.get("synthesis_key") != key or
                sidecar.get("profile_id") != profile_id or sidecar.get("audio_sha256") != checked[3]):
            raise ValueError(f"synthesis sidecar does not match current speech for {segment.id}")
        entries[str(segment.id)] = {
            "segment": segment,
            "speech_hash": speech_hash,
            "synthesis_key": key,
            "profile_id": profile_id,
            "cache_path": cache_path,
            "audio_sha256": checked[3],
            "sample_rate": checked[0],
            "channels": checked[1],
            "frames": checked[2],
            "composition": dict(profile.get("composition", {})),
        }
    return entries


def _write_silence(project: Project | None, sample_rate: int, frames: int) -> tuple[Any, str]:
    if frames <= 0:
        raise ValueError("silence frame count must be positive")
    if project is not None:
        path = project.root / "composition" / "parts" / f"silence-{sample_rate}-{frames}.wav"
        if not path.is_file() or sf.info(path).frames != frames:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.tmp")
            sf.write(
                temporary,
                np.zeros(frames, dtype=np.float32),
                sample_rate,
                subtype="PCM_16",
                format="WAV",
            )
            temporary.replace(path)
        digest = hash_file(path)
        return AudioFileSource(path, expected_sha256=digest, sample_rate=sample_rate, frames=frames), digest
    audio = np.zeros(frames, dtype=np.float32)
    return AudioBufferSource(audio, sample_rate), hashlib.sha256(audio.tobytes()).hexdigest()


def _build_layout(
    project: Project | None,
    segments: list[tuple[Any, Mapping[str, Any]]],
    *,
    target_lufs: float | None,
    true_peak_ceiling_dbtp: float | None,
    peak_policy: str,
    clip_policy: str,
    composition: Mapping[str, Any] | None = None,
) -> tuple[AudioJob, dict[str, Any]]:
    if not segments:
        raise ValueError("composition selected no synthesized segments")
    sample_rate = int(segments[0][1]["sample_rate"])
    items: list[Any] = []
    layout: list[dict[str, Any]] = []
    seen_events: set[str] = set()
    def append_silence(segment: Any, side: str, pause: Any) -> None:
        seconds, event_ids = _pause_seconds(pause)
        unique_events = tuple(event_id for event_id in event_ids if event_id not in seen_events)
        if unique_events:
            seen_events.update(unique_events)
        if seconds <= 0 or (event_ids and not unique_events):
            return
        frames = seconds_to_frames(seconds, sample_rate)
        if frames <= 0:
            return
        source, silence_hash = _write_silence(project, sample_rate, frames)
        segment_id = str(segment.id)
        item_id = f"silence-{segment_id}-{side}"
        items.append(
            AudioClip(
                id=item_id,
                source=source,
                metadata={
                    "kind": "silence",
                    "segment_id": segment_id,
                    "side": side,
                    "event_ids": list(unique_events),
                    "frames": frames,
                },
            )
        )
        layout.append(
            {
                "id": item_id,
                "kind": "silence",
                "segment_id": segment_id,
                "side": side,
                "frames": frames,
                "event_ids": list(unique_events),
                "audio_sha256": silence_hash,
            }
        )

    for segment, entry in segments:
        segment_id = str(segment.id)
        append_silence(segment, "before", getattr(segment, "pause_before", None))
        operations = _speech_operations(segment, composition or entry.get("composition", {}))
        part = (project.root / "composition" / "parts" / f"{segment_id}.wav") if project else None
        source_path = entry["cache_path"]
        if part is not None:
            part.parent.mkdir(parents=True, exist_ok=True)
            part.write_bytes(source_path.read_bytes())
            source = AudioFileSource(
                part,
                expected_sha256=entry["audio_sha256"],
                sample_rate=int(entry["sample_rate"]),
                channels=int(entry["channels"]),
                frames=int(entry["frames"]),
            )
        else:
            audio, rate = sf.read(source_path, always_2d=False, dtype="float32")
            source = AudioBufferSource(audio, rate)
        items.append(
            AudioClip(
                id=segment_id,
                source=source,
                operations=operations,
                metadata={
                    "kind": "speech",
                    "segment_id": segment_id,
                    "speech_hash": entry["speech_hash"],
                    "synthesis_key": entry["synthesis_key"],
                    "audio_sha256": entry["audio_sha256"],
                    "operations": _operation_payload(operations),
                },
            )
        )
        layout.append(
            {
                "id": segment_id,
                "kind": "speech",
                "segment_id": segment_id,
                "speech_hash": entry["speech_hash"],
                "synthesis_key": entry["synthesis_key"],
                "audio_sha256": entry["audio_sha256"],
                "operations": _operation_payload(operations),
            }
        )
        append_silence(segment, "after", getattr(segment, "pause_after", None))
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
        "schema": "readio.composition.v2",
        "items": layout,
        **policy_payload,
    }
    job = AudioJob(
        items=tuple(items),
        output=OutputPolicy(
            sample_rate=sample_rate,
            channels=1,
            loudness=LoudnessPolicy(target_lufs, true_peak_ceiling_dbtp, peak_policy),
            clip_policy=clip_policy,
        ),
        producer={"readio": "project"},
        source={"project_id": project.manifest.project_id if project else None, "composition_id": composition_id(identity_payload)},
    )
    return job, {"identity_payload": identity_payload, "composition_id": composition_id(identity_payload), "layout": layout}


def build_audio_job(
    project: Project,
    *,
    target_lufs: float | None = None,
    true_peak_ceiling_dbtp: float | None = -1.0,
    peak_policy: str = "reduce_gain",
    clip_policy: str = "clamp",
) -> tuple[AudioJob, dict[str, Any]]:
    plan = load_scope_plan(project)
    entries = _cache_entries(project, plan)
    return _build_layout(
        project,
        [(segment, entries[str(segment.id)]) for segment in plan.segments],
        target_lufs=target_lufs,
        true_peak_ceiling_dbtp=true_peak_ceiling_dbtp,
        peak_policy=peak_policy,
        clip_policy=clip_policy,
        composition=entries[str(plan.segments[0].id)].get("composition", {}),
    )


def _write_composition_result(project: Project, job: AudioJob, identity: Mapping[str, Any], result: Any) -> dict[str, Any]:
    audiojob_path = project.paths["composition_audiojob"]
    job.save(audiojob_path)
    master = project.paths["composition_master"]
    temporary = master.with_name(f".{master.name}.tmp")
    sf.write(temporary, result.audio, result.sample_rate, subtype="PCM_16", format="WAV")
    temporary.replace(master)
    timeline = {
        "format": "readio.composition-timeline",
        "schema_version": 2,
        "sample_rate": result.sample_rate,
        "composition_id": identity["composition_id"],
        "layout": list(identity["layout"]),
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
            {"id": marker.id, "sample_offset": marker.sample_offset, "name": marker.name, "item_id": marker.item_id}
            for marker in result.markers
        ],
    }
    atomic_write_json(project.paths["composition_timeline"], timeline)
    master_sha = hash_file(master)
    atomic_write_json(
        project.paths["composition_state"],
        {
            "format": "readio.composition-state",
            "schema_version": 2,
            "composition_id": identity["composition_id"],
            "identity_payload": identity["identity_payload"],
            "synthesis_trace_sha256": (
                hash_file(project.paths["synthesis_trace"])
                if project.paths["synthesis_trace"].is_file()
                else None
            ),
            "synthesis_profile_id": read_json(project.paths["synthesis_profile"]).get("profile_id"),
            "audiojob_sha256": hash_file(audiojob_path),
            "master_sha256": master_sha,
            "timeline_sha256": hash_file(project.paths["composition_timeline"]),
            "sample_rate": result.sample_rate,
            "frames": len(result.audio),
            "loudness": {
                "integrated_lufs_before": result.loudness.before.integrated_lufs if result.loudness else None,
                "integrated_lufs_after": result.loudness.after.integrated_lufs if result.loudness else None,
                "gain_db": result.loudness.applied_gain_db if result.loudness else 0.0,
            },
        },
    )
    return {"composition_id": identity["composition_id"], "master": master, "frames": len(result.audio), "items": len(result.items)}


def compose_project(
    project: Project,
    *,
    target_lufs: float | None = None,
    true_peak_ceiling_dbtp: float | None = -1.0,
    peak_policy: str = "reduce_gain",
    clip_policy: str = "clamp",
    on_progress: CompositionProgressCallback | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    with project_lock(project, operation="compose"):
        if on_phase is not None:
            on_phase("Preparing composition")
        job, identity = build_audio_job(
            project,
            target_lufs=target_lufs,
            true_peak_ceiling_dbtp=true_peak_ceiling_dbtp,
            peak_policy=peak_policy,
            clip_policy=clip_policy,
        )
        result = Composer().compose(job, on_progress=on_progress)
        if on_phase is not None:
            on_phase("Writing composition artifacts")
        return _write_composition_result(project, job, identity, result)


def compose_artifacts(
    artifacts: Any,
    *,
    plan: Any | None = None,
    composition: Mapping[str, Any] | None = None,
    target_lufs: float | None = None,
    true_peak_ceiling_dbtp: float | None = -1.0,
    peak_policy: str = "reduce_gain",
    clip_policy: str = "clamp",
    output: Path | None = None,
    on_progress: CompositionProgressCallback | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    artifact_list = tuple(artifacts)
    if not artifact_list:
        raise ValueError("preview selected no synthesized segments")
    if plan is None:
        clips = tuple(
            AudioClip(
                id=artifact.segment_id,
                source=AudioFileSource(
                    artifact.cache_path,
                    expected_sha256=artifact.audio_sha256,
                    sample_rate=artifact.sample_rate,
                    channels=artifact.channels,
                    frames=artifact.frames,
                ),
                metadata={"kind": "speech", "segment_id": artifact.segment_id, "speech_hash": artifact.speech_hash or artifact.content_hash, "synthesis_key": artifact.synthesis_key},
            )
            for artifact in artifact_list
        )
        job = AudioJob(items=clips, output=OutputPolicy(sample_rate=clips[0].source.sample_rate or 24000, channels=1, loudness=LoudnessPolicy(target_lufs, true_peak_ceiling_dbtp, peak_policy), clip_policy=clip_policy))
        identity = {"composition_id": composition_id({"schema": "readio.composition.v2", "items": [clip.metadata for clip in clips]}), "layout": [dict(clip.metadata) for clip in clips]}
    else:
        by_id = {artifact.segment_id: artifact for artifact in artifact_list}
        segments = [(segment, {"cache_path": by_id[str(segment.id)].cache_path, "audio_sha256": by_id[str(segment.id)].audio_sha256, "sample_rate": by_id[str(segment.id)].sample_rate, "channels": by_id[str(segment.id)].channels, "frames": by_id[str(segment.id)].frames, "speech_hash": by_id[str(segment.id)].speech_hash or by_id[str(segment.id)].content_hash, "synthesis_key": by_id[str(segment.id)].synthesis_key}) for segment in plan.segments if str(segment.id) in by_id]
        job, identity = _build_layout(
            None,
            segments,
            target_lufs=target_lufs,
            true_peak_ceiling_dbtp=true_peak_ceiling_dbtp,
            peak_policy=peak_policy,
            clip_policy=clip_policy,
            composition=composition,
        )
    result = Composer().compose(job, on_progress=on_progress)
    if output is not None:
        if on_phase is not None:
            on_phase("Writing preview artifacts")
        output.parent.mkdir(parents=True, exist_ok=True)
        sf.write(output, result.audio, result.sample_rate, subtype="PCM_16", format="WAV")
    return {"sample_rate": result.sample_rate, "frames": len(result.audio), "items": len(result.items), "output": output, "composition_id": identity["composition_id"]}


__all__ = ["build_audio_job", "compose_artifacts", "compose_project", "composition_id", "seconds_to_frames"]
