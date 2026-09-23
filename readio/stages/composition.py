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


def _cache_entries(project: Project, plan: Any, scope_id: str) -> dict[tuple[str, str], dict[str, Any]]:
    profile = read_json(project.paths["synthesis_profile"])
    canonical = profile.get("canonical")
    profile_id = str(profile.get("profile_id", ""))
    if not isinstance(canonical, dict) or not profile_id:
        raise ValueError("synthesis profile is missing canonical identity")
    entries: dict[tuple[str, str], dict[str, Any]] = {}
    cache_dir = project.root / "synthesis" / "cache"
    for segment in plan.segments:
        speech_hash = segment_speech_hash(plan, segment, canonical)
        key = segment_synthesis_key(speech_hash, profile_id)
        cache_path = cache_dir / f"{key.replace(':', '-')}.wav"
        sidecar_path = cache_dir / f"{key.replace(':', '-')}.json"
        checked = _valid_audio(cache_path)
        if checked is None:
            raise ValueError(f"synthesis audio is missing or corrupt for {scope_id}:{segment.id}")
        try:
            sidecar = read_json(sidecar_path)
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"synthesis sidecar is missing or corrupt for {scope_id}:{segment.id}"
            ) from exc
        if (
            sidecar.get("speech_hash") != speech_hash
            or sidecar.get("synthesis_key") != key
            or sidecar.get("profile_id") != profile_id
            or sidecar.get("audio_sha256") != checked[3]
        ):
            raise ValueError(f"synthesis sidecar does not match current speech for {scope_id}:{segment.id}")
        entries[(scope_id, str(segment.id))] = {
            "scope_id": scope_id,
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
    scope_metadata: tuple[Mapping[str, Any], ...] = (),
) -> tuple[AudioJob, dict[str, Any]]:
    if not segments:
        raise ValueError("composition selected no synthesized segments")
    sample_rate = int(segments[0][1]["sample_rate"])
    items: list[Any] = []
    layout: list[dict[str, Any]] = []
    seen_events: set[str] = set()
    def append_silence(segment: Any, entry: Mapping[str, Any], side: str, pause: Any) -> None:
        seconds, event_ids = _pause_seconds(pause)
        scope_id = str(entry.get("scope_id", "document"))
        scoped_events = tuple(
            f"{scope_id}:{event_id}" if scope_id != "document" else event_id
            for event_id in event_ids
        )
        unique_events = tuple(event_id for event_id in scoped_events if event_id not in seen_events)
        if unique_events:
            seen_events.update(unique_events)
        if seconds <= 0 or (event_ids and not unique_events):
            return
        frames = seconds_to_frames(seconds, sample_rate)
        if frames <= 0:
            return
        source, silence_hash = _write_silence(project, sample_rate, frames)
        segment_id = str(segment.id)
        qualified_id = segment_id if scope_id == "document" else f"{scope_id}:{segment_id}"
        item_id = f"silence-{qualified_id}-{side}"
        metadata = {
            "kind": "silence",
            "segment_id": segment_id,
            "side": side,
            "event_ids": list(unique_events),
            "frames": frames,
        }
        if scope_id != "document":
            metadata["scope_id"] = scope_id
        items.append(AudioClip(id=item_id, source=source, metadata=metadata))
        layout_item = {
            "id": item_id,
            "kind": "silence",
            "segment_id": segment_id,
            "side": side,
            "frames": frames,
            "event_ids": list(unique_events),
            "audio_sha256": silence_hash,
        }
        if scope_id != "document":
            layout_item["scope_id"] = scope_id
        layout.append(layout_item)

    for segment, entry in segments:
        scope_id = str(entry.get("scope_id", "document"))
        segment_id = str(segment.id)
        qualified_id = segment_id if scope_id == "document" else f"{scope_id}:{segment_id}"
        append_silence(segment, entry, "before", getattr(segment, "pause_before", None))
        operations = _speech_operations(segment, composition or entry.get("composition", {}))
        if project is None:
            part = None
        elif scope_id == "document":
            part = project.root / "composition" / "parts" / f"{segment_id}.wav"
        else:
            part = project.root / "composition" / "parts" / scope_id / f"{segment_id}.wav"
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
        metadata = {
            "kind": "speech",
            "segment_id": segment_id,
            "speech_hash": entry["speech_hash"],
            "synthesis_key": entry["synthesis_key"],
            "audio_sha256": entry["audio_sha256"],
            "operations": _operation_payload(operations),
        }
        if scope_id != "document":
            metadata["scope_id"] = scope_id
        items.append(
            AudioClip(
                id=qualified_id,
                source=source,
                operations=operations,
                metadata=metadata,
            )
        )
        layout_item = {
            "id": qualified_id,
            "kind": "speech",
            "segment_id": segment_id,
            "speech_hash": entry["speech_hash"],
            "synthesis_key": entry["synthesis_key"],
            "audio_sha256": entry["audio_sha256"],
            "operations": _operation_payload(operations),
        }
        if scope_id != "document":
            layout_item["scope_id"] = scope_id
        layout.append(layout_item)
        append_silence(segment, entry, "after", getattr(segment, "pause_after", None))
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
    if len(scope_metadata) > 1 or any(item.get("kind") == "chapter" for item in scope_metadata):
        identity_payload["schema"] = "readio.composition.v3"
        identity_payload["scopes"] = [dict(item) for item in scope_metadata]
        identity_payload["chapters"] = [
            dict(item) for item in scope_metadata if item.get("kind") == "chapter"
        ]
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
    scoped_plans = tuple(
        (scope, load_scope_plan(project, scope))
        for scope in project.load_plan_index().scopes
    )
    segments = []
    first_entry: dict[str, Any] | None = None
    document_scopes = {item.id: item for item in project.document_scopes()}
    scope_metadata = []
    for scope, plan in scoped_plans:
        document_scope = document_scopes.get(scope.id)
        scope_metadata.append(
            {
                "scope_id": scope.id,
                "kind": document_scope.kind if document_scope is not None else scope.kind,
                "title": (
                    document_scope.title if document_scope is not None else scope.title
                ),
                "source_number": (
                    document_scope.source_number if document_scope is not None else None
                ),
                "plan_id": plan.plan_id,
                "plan_sha256": hash_file(project.root / "plan" / scope.path),
            }
        )
        entries = _cache_entries(project, plan, scope.id)
        if first_entry is None and entries:
            first_entry = next(iter(entries.values()))
        segments.extend(
            (segment, entries[(scope.id, str(segment.id))])
            for segment in plan.segments
        )
    if first_entry is None:
        raise ValueError("project plans contain no composition segments")
    return _build_layout(
        project,
        segments,
        target_lufs=target_lufs,
        true_peak_ceiling_dbtp=true_peak_ceiling_dbtp,
        peak_policy=peak_policy,
        clip_policy=clip_policy,
        composition=first_entry.get("composition", {}),
        scope_metadata=tuple(scope_metadata),
    )


def _write_composition_result(project: Project, job: AudioJob, identity: Mapping[str, Any], result: Any) -> dict[str, Any]:
    audiojob_path = project.paths["composition_audiojob"]
    job.save(audiojob_path)
    master = project.paths["composition_master"]
    temporary = master.with_name(f".{master.name}.tmp")
    sf.write(temporary, result.audio, result.sample_rate, subtype="PCM_16", format="WAV")
    temporary.replace(master)
    chapter_metadata = identity.get("identity_payload", {}).get("chapters", [])
    item_ranges = {
        item.item_id: (item.start_sample, item.end_sample) for item in result.items
    }
    layout_items = identity.get("layout", [])
    timeline_chapters = []
    previous_end = 0
    for chapter in chapter_metadata:
        scope_id = str(chapter.get("scope_id", ""))
        ranges = [
            item_ranges[layout_item["id"]]
            for layout_item in layout_items
            if layout_item.get("scope_id") == scope_id
            and layout_item.get("id") in item_ranges
        ]
        start_sample = min((start for start, _ in ranges), default=previous_end)
        timeline_chapters.append(
            {
                "scope_id": scope_id,
                "source_number": chapter.get("source_number"),
                "title": chapter.get("title"),
                "start_sample": start_sample,
            }
        )
        previous_end = max((end for _, end in ranges), default=previous_end)
    timeline = {
        "format": "readio.composition-timeline",
        "schema_version": 2,
        "sample_rate": result.sample_rate,
        "composition_id": identity["composition_id"],
        "layout": list(identity["layout"]),
        **({"chapters": timeline_chapters} if timeline_chapters else {}),
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
    plans: Any | None = None,
    scope_metadata: tuple[Mapping[str, Any], ...] = (),
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
    plan_pairs = (
        tuple(plans)
        if plans is not None
        else (("document", plan),) if plan is not None else ()
    )
    if not plan_pairs:
        clips = []
        for artifact in artifact_list:
            scope_id = str(getattr(artifact, "scope_id", "document"))
            segment_id = artifact.segment_id
            qualified_id = (
                segment_id if scope_id == "document" else f"{scope_id}:{segment_id}"
            )
            metadata = {
                "kind": "speech",
                "segment_id": segment_id,
                "speech_hash": artifact.speech_hash or artifact.content_hash,
                "synthesis_key": artifact.synthesis_key,
            }
            if scope_id != "document":
                metadata["scope_id"] = scope_id
            clips.append(
                AudioClip(
                    id=qualified_id,
                    source=AudioFileSource(
                        artifact.cache_path,
                        expected_sha256=artifact.audio_sha256,
                        sample_rate=artifact.sample_rate,
                        channels=artifact.channels,
                        frames=artifact.frames,
                    ),
                    metadata=metadata,
                )
            )
        job = AudioJob(
            items=tuple(clips),
            output=OutputPolicy(
                sample_rate=clips[0].source.sample_rate or 24000,
                channels=1,
                loudness=LoudnessPolicy(
                    target_lufs, true_peak_ceiling_dbtp, peak_policy
                ),
                clip_policy=clip_policy,
            ),
        )
        identity = {
            "composition_id": composition_id(
                {"schema": "readio.composition.v2", "items": [clip.metadata for clip in clips]}
            ),
            "layout": [dict(clip.metadata) for clip in clips],
        }
    else:
        by_id = {
            (str(getattr(artifact, "scope_id", "document")), artifact.segment_id): artifact
            for artifact in artifact_list
        }
        segments = []
        for scope_id, scope_plan in plan_pairs:
            for segment in scope_plan.segments:
                artifact = by_id.get((str(scope_id), str(segment.id)))
                if artifact is None:
                    continue
                entry = {
                    "scope_id": str(scope_id),
                    "cache_path": artifact.cache_path,
                    "audio_sha256": artifact.audio_sha256,
                    "sample_rate": artifact.sample_rate,
                    "channels": artifact.channels,
                    "frames": artifact.frames,
                    "speech_hash": artifact.speech_hash or artifact.content_hash,
                    "synthesis_key": artifact.synthesis_key,
                }
                segments.append((segment, entry))
        job, identity = _build_layout(
            None,
            segments,
            target_lufs=target_lufs,
            true_peak_ceiling_dbtp=true_peak_ceiling_dbtp,
            peak_policy=peak_policy,
            clip_policy=clip_policy,
            composition=composition,
            scope_metadata=scope_metadata,
        )
    result = Composer().compose(job, on_progress=on_progress)
    if output is not None:
        if on_phase is not None:
            on_phase("Writing preview artifacts")
        output.parent.mkdir(parents=True, exist_ok=True)
        sf.write(output, result.audio, result.sample_rate, subtype="PCM_16", format="WAV")
    return {"sample_rate": result.sample_rate, "frames": len(result.audio), "items": len(result.items), "output": output, "composition_id": identity["composition_id"]}


__all__ = ["build_audio_job", "compose_artifacts", "compose_project", "composition_id", "seconds_to_frames"]
