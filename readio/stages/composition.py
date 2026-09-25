"""Composition of canonical project speech artifacts without TTS access."""

from __future__ import annotations

import hashlib
import logging
import math
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from audiocompose import (
    AudioAnchor,
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
    RatePitchEnvelope,
    Tempo,
)
from utterplan import parse_duration

from ..errors import InputError
from ..project import Project, atomic_write_json, canonical_json, hash_file, project_lock, read_json
from ..stages.speech_identity import segment_speech_hash, segment_synthesis_key
from .planning import load_scope_plan
from .synthesis import _valid_audio

_LOGGER = logging.getLogger(__name__)


def composition_id(payload: Mapping[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(payload)).hexdigest()}"


def seconds_to_frames(seconds: float, sample_rate: int) -> int:
    """Use the one composition rounding rule for layout and silence audio."""
    return round(float(seconds) * sample_rate)


_SSMD_RATE_FACTORS = {
    "very-slow": 0.65,
    "slow": 0.80,
    "moderate": 0.90,
    "normal": 1.00,
    "brisk": 1.10,
    "fast": 1.25,
    "very-fast": 1.50,
}
_SSMD_PITCH_PERCENTAGES = {
    "very-low": -20.0,
    "low": -12.0,
    "moderate-low": -6.0,
    "normal": 0.0,
    "moderate-high": 6.0,
    "high": 12.0,
    "very-high": 20.0,
}
_SSMD_VOLUME_DB = {
    "silent": -60.0,
    "x-soft": -12.0,
    "soft": -6.0,
    "medium": 0.0,
    "loud": 6.0,
    "x-loud": 12.0,
}
_SSMD_VOLUME_LEVEL_DB = {"0": -60.0, "1": -12.0, "2": -6.0, "3": 0.0, "4": 6.0, "5": 12.0}
_APPLICATION_RATE_VALUES = {
    "x-slow": 0.65,
    "slow": 0.8,
    "medium": 1.0,
    "fast": 1.25,
    "x-fast": 1.5,
}
_APPLICATION_PITCH_VALUES = {
    "x-low": -4.0,
    "low": -2.0,
    "medium": 0.0,
    "high": 2.0,
    "x-high": 4.0,
}


def _application_numeric(value: Any, values: Mapping[str, float], default: float) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return float(values.get(str(value).strip().lower(), default))


def _unsupported_ssmd_prosody(field: str, value: object) -> InputError:
    return InputError(
        f"unsupported SSMD 0.9 {field} value: {value!r}",
        code=f"composition.prosody.{field}_unsupported",
        details={"field": field, "value": repr(value)},
    )


def _ssmd_rate_factor(value: object) -> float:
    if isinstance(value, str):
        normalized = value.strip().lower()
        natural = _SSMD_RATE_FACTORS.get(normalized)
        if natural is not None:
            return natural
        match = re.fullmatch(r"([+-]?)(\d+(?:\.\d+)?)%", normalized)
        if match is not None:
            percentage = float(match.group(2))
            factor = (
                1.0 + percentage / 100.0
                if match.group(1) == "+"
                else 1.0 - percentage / 100.0
                if match.group(1) == "-"
                else percentage / 100.0
            )
            if math.isfinite(factor) and factor > 0.0:
                return factor
    raise _unsupported_ssmd_prosody("rate", value)


def _ssmd_pitch_semitones(value: object) -> float:
    percentage: float | None = None
    if isinstance(value, str):
        normalized = value.strip().lower()
        percentage = _SSMD_PITCH_PERCENTAGES.get(normalized)
        if percentage is None:
            match = re.fullmatch(r"([+-]?)(\d+(?:\.\d+)?)%", normalized)
            if match is not None:
                magnitude = float(match.group(2))
                percentage = -magnitude if match.group(1) == "-" else magnitude
    if percentage is not None:
        ratio = 1.0 + percentage / 100.0
        if math.isfinite(ratio) and ratio > 0.0:
            return 12.0 * math.log2(ratio)
    raise _unsupported_ssmd_prosody("pitch", value)


def _ssmd_volume_db(value: object) -> float:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _SSMD_VOLUME_DB:
            return _SSMD_VOLUME_DB[normalized]
        if normalized in _SSMD_VOLUME_LEVEL_DB:
            return _SSMD_VOLUME_LEVEL_DB[normalized]
        match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)db", normalized)
        if match is not None:
            decibels = float(match.group(1))
            if math.isfinite(decibels):
                return decibels
    raise _unsupported_ssmd_prosody("volume", value)


def _effective_rate_factor(segment: Any, composition: Mapping[str, Any]) -> float:
    directives = getattr(segment, "directives", None)
    prosody = getattr(directives, "prosody", None)
    value = getattr(prosody, "rate", None)
    if value is not None:
        return _ssmd_rate_factor(value)
    return _application_numeric(
        composition.get("rate", composition.get("speed")),
        _APPLICATION_RATE_VALUES,
        1.0,
    )


def _effective_pitch_semitones(segment: Any, composition: Mapping[str, Any]) -> float:
    directives = getattr(segment, "directives", None)
    prosody = getattr(directives, "prosody", None)
    value = getattr(prosody, "pitch", None)
    if value is not None:
        return _ssmd_pitch_semitones(value)
    return _application_numeric(
        composition.get("pitch"),
        _APPLICATION_PITCH_VALUES,
        0.0,
    )


def _speech_operations(
    segment: Any,
    composition: Mapping[str, Any] | None = None,
    *,
    envelope: RatePitchEnvelope | None = None,
) -> tuple[Any, ...]:
    composition = composition or {}
    directives = getattr(segment, "directives", None)
    prosody = getattr(directives, "prosody", None)
    operations: list[Any] = []
    rate = _effective_rate_factor(segment, composition)
    if (envelope is None or not envelope.rate) and rate != 1.0:
        operations.append(Tempo(rate))
    pitch = _effective_pitch_semitones(segment, composition)
    if (envelope is None or not envelope.pitch_semitones) and pitch:
        operations.append(PitchShift(pitch))
    if envelope is not None:
        operations.append(envelope)
    volume_value = getattr(prosody, "volume", None)
    volume = (
        _ssmd_volume_db(volume_value)
        if volume_value is not None
        else _application_numeric(
            composition.get("volume"),
            _SSMD_VOLUME_DB,
            0.0,
        )
    )
    if volume:
        operations.append(Gain(volume))
    emphasis = getattr(directives, "emphasis", None)
    emphasis_value = getattr(emphasis, "level", None)
    if emphasis_value is None:
        emphasis_value = composition.get("emphasis")
    emphasis_gain = _application_numeric(
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
        operations.append(FadeIn(_application_numeric(fade_in, {}, 0.0)))
    if fade_out is not None:
        operations.append(FadeOut(_application_numeric(fade_out, {}, 0.0)))
    return tuple(operations)


def _segment_voice_reference(segment: Any) -> str | None:
    directives = getattr(segment, "directives", None)
    voice = getattr(directives, "voice", None)
    reference = (
        voice.get("reference") if isinstance(voice, Mapping) else getattr(voice, "reference", None)
    )
    return reference if isinstance(reference, str) and reference else None


def _segment_voice_identity(
    profile: Mapping[str, Any] | None, plan: Any, scope_id: str, segment: Any
) -> str:
    profile_payload = profile or {}
    canonical = profile_payload.get("canonical", {})
    canonical = canonical if isinstance(canonical, Mapping) else {}
    reference = _segment_voice_reference(segment)
    if reference is not None:
        scoped_bindings = canonical.get("bindings_by_scope", {})
        if isinstance(scoped_bindings, Mapping):
            bindings = scoped_bindings.get(scope_id, {})
            if isinstance(bindings, Mapping) and isinstance(bindings.get(reference), str):
                return f"target:{bindings[reference]}"
        project_bindings = profile_payload.get("project_voice_bindings", {})
        provider = (
            project_bindings.get("provider") if isinstance(project_bindings, Mapping) else None
        )
        metadata = getattr(plan, "document_metadata", {})
        document_bindings = (
            metadata.get("voice_bindings", {}) if isinstance(metadata, Mapping) else {}
        )
        if isinstance(document_bindings, Mapping):
            if provider is not None and isinstance(document_bindings.get(provider), Mapping):
                target = document_bindings[provider].get(reference)
            else:
                target = document_bindings.get(reference)
                if target is None and len(document_bindings) == 1:
                    only_bindings = next(iter(document_bindings.values()))
                    target = (
                        only_bindings.get(reference) if isinstance(only_bindings, Mapping) else None
                    )
            if isinstance(target, str):
                return f"target:{target}"
        project_role_bindings = (
            project_bindings.get("bindings", {}) if isinstance(project_bindings, Mapping) else {}
        )
        if isinstance(project_role_bindings, Mapping):
            target = project_role_bindings.get(reference)
            if isinstance(target, str):
                return f"target:{target}"
        return f"role:{reference}"

    target = canonical.get("target_id") or canonical.get("voice")
    targets = canonical.get("targets")
    if target is None and isinstance(targets, Mapping) and len(targets) == 1:
        target = next(iter(targets))
    selections = canonical.get("selections")
    if target is None and isinstance(selections, Mapping) and len(selections) == 1:
        selection = next(iter(selections.values()))
        if isinstance(selection, Mapping):
            target = selection.get("target_id") or selection.get("voice")
    if isinstance(target, str) and target:
        return f"target:{target}"
    return f"profile:{hashlib.sha256(canonical_json(dict(canonical))).hexdigest()}"


def _transition_seconds(policy: Mapping[str, Any], field: str) -> float:
    value = policy.get(field)
    return float(parse_duration(value)) if value is not None else 0.0


def _operation_payload(operations: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [dict(operation.to_dict()) for operation in operations]


def _pause_seconds(pause: Any) -> tuple[float, tuple[str, ...]]:
    if pause is None:
        return 0.0, ()
    return float(getattr(pause, "seconds", 0.0)), tuple(getattr(pause, "events", ()) or ())


def _markers_by_segment(
    plan: Any, scope_id: str = "document"
) -> dict[str, tuple[dict[str, Any], ...]]:
    markers: dict[str, list[dict[str, Any]]] = {str(segment.id): [] for segment in plan.segments}
    for marker in plan.markers:
        owner = next(
            (
                segment
                for segment in plan.segments
                if segment.spoken_start <= marker.spoken_position <= segment.spoken_end
            ),
            None,
        )
        if owner is not None:
            value = marker.to_dict()
            if scope_id != "document":
                value["id"] = f"{scope_id}:{marker.id}"
            markers[str(owner.id)].append(value)
    return {segment_id: tuple(values) for segment_id, values in markers.items()}


def _marker_sample_offset(marker: Mapping[str, Any], segment: Any, entry: Mapping[str, Any]) -> int:
    position = max(0, min(len(segment.text), int(marker["spoken_position"]) - segment.spoken_start))
    frames = int(entry["frames"])
    timings = entry.get("word_timings", ())
    for timing in timings:
        char_start = int(
            timing.get("char_start", 0) if isinstance(timing, Mapping) else timing.char_start
        )
        char_end = int(
            timing.get("char_end", 0) if isinstance(timing, Mapping) else timing.char_end
        )
        start_sample = int(
            timing.get("start_sample", 0) if isinstance(timing, Mapping) else timing.start_sample
        )
        end_sample = int(
            timing.get("end_sample", 0) if isinstance(timing, Mapping) else timing.end_sample
        )
        if char_start <= position <= char_end:
            if char_end == char_start:
                return start_sample
            fraction = (position - char_start) / (char_end - char_start)
            return round(start_sample + fraction * (end_sample - start_sample))
    if not segment.text:
        return 0
    return round(frames * position / len(segment.text))


def _cache_entries(
    project: Project, plan: Any, scope_id: str
) -> dict[tuple[str, str], dict[str, Any]]:
    profile = read_json(project.paths["synthesis_profile"])
    canonical = profile.get("canonical")
    profile_id = str(profile.get("profile_id", ""))
    if not isinstance(canonical, dict) or not profile_id:
        raise ValueError("synthesis profile is missing canonical identity")
    entries: dict[tuple[str, str], dict[str, Any]] = {}
    cache_dir = project.root / "synthesis" / "cache"
    markers_by_segment = _markers_by_segment(plan, scope_id)
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
            raise ValueError(
                f"synthesis sidecar does not match current speech for {scope_id}:{segment.id}"
            )
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
            "word_timings": tuple(sidecar.get("word_timings", ())),
            "markers": markers_by_segment[str(segment.id)],
            "composition": dict(profile.get("composition", {})),
            "prosody_transitions": (
                plan.document_metadata.get("prosody_transitions")
                if isinstance(getattr(plan, "document_metadata", None), Mapping)
                else None
            ),
            "voice_identity": _segment_voice_identity(profile, plan, scope_id, segment),
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
        return AudioFileSource(
            path, expected_sha256=digest, sample_rate=sample_rate, frames=frames
        ), digest
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
    output_sample_rate: int | None = None,
) -> tuple[AudioJob, dict[str, Any]]:
    if not segments:
        raise ValueError("composition selected no synthesized segments")
    sample_rate = int(output_sample_rate or segments[0][1]["sample_rate"])
    items: list[Any] = []
    layout: list[dict[str, Any]] = []
    seen_events: set[str] = set()
    transition_policies: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    warned_volume_scopes: set[str] = set()
    previous_scope_id: str | None = None
    previous_prosody: tuple[float, float, str] | None = None

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
        if scope_id != previous_scope_id:
            previous_scope_id = scope_id
            previous_prosody = None
        transition_value = entry.get("prosody_transitions")
        transition_policy: dict[str, Any] | None = None
        transition_enabled = False
        same_voice_only = True
        rate_duration = 0.0
        pitch_duration = 0.0
        if transition_value is not None:
            if not isinstance(transition_value, Mapping):
                raise InputError(
                    "invalid Utterplan prosody_transitions metadata",
                    code="composition.prosody_transitions_invalid",
                )
            transition_policy = dict(transition_value)
            transition_policies[scope_id] = transition_policy
            enabled_value = transition_policy.get("enabled", True)
            same_voice_value = transition_policy.get("same_voice_only", True)
            if not isinstance(enabled_value, bool) or not isinstance(same_voice_value, bool):
                raise InputError(
                    "invalid Utterplan prosody_transitions metadata",
                    code="composition.prosody_transitions_invalid",
                )
            transition_enabled = enabled_value
            same_voice_only = same_voice_value
            rate_duration = _transition_seconds(transition_policy, "rate")
            pitch_duration = _transition_seconds(transition_policy, "pitch")
            volume_duration = _transition_seconds(transition_policy, "volume")
            if (
                transition_enabled
                and volume_duration > 0.0
                and scope_id not in warned_volume_scopes
            ):
                warning = (
                    "AudioJob v2 does not support continuous volume transitions; "
                    f"using static volume for scope {scope_id!r}."
                )
                warnings.append(warning)
                warned_volume_scopes.add(scope_id)
                _LOGGER.warning("%s", warning)
        segment_id = str(segment.id)
        qualified_id = segment_id if scope_id == "document" else f"{scope_id}:{segment_id}"
        append_silence(segment, entry, "before", getattr(segment, "pause_before", None))
        segment_composition = composition or entry.get("composition", {})
        rate = _effective_rate_factor(segment, segment_composition)
        pitch = _effective_pitch_semitones(segment, segment_composition)
        voice_identity = entry.get("voice_identity") or _segment_voice_identity(
            None, None, scope_id, segment
        )
        envelope = None
        if transition_enabled and previous_prosody is not None:
            previous_rate, previous_pitch, previous_voice = previous_prosody
            same_voice = not same_voice_only or previous_voice == voice_identity
            if same_voice and (previous_rate != rate or previous_pitch != pitch):
                clip_seconds = int(entry["frames"]) / int(entry["sample_rate"])
                envelope = RatePitchEnvelope.transition(
                    from_rate=previous_rate,
                    to_rate=rate,
                    rate_seconds=min(rate_duration, clip_seconds),
                    from_semitones=previous_pitch,
                    to_semitones=pitch,
                    pitch_seconds=min(pitch_duration, clip_seconds),
                )
        operations = _speech_operations(segment, segment_composition, envelope=envelope)
        previous_prosody = (rate, pitch, voice_identity)
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
        elif entry.get("audio") is not None:
            source = AudioBufferSource(
                np.asarray(entry["audio"], dtype=np.float32), int(entry["sample_rate"])
            )
        else:
            audio, source_rate = sf.read(source_path, always_2d=False, dtype="float32")
            source = AudioBufferSource(audio, source_rate)
        markers = tuple(entry.get("markers", ()))
        anchors = tuple(
            AudioAnchor(
                id=str(marker["id"]),
                sample_offset=_marker_sample_offset(marker, segment, entry),
                name=marker.get("name"),
            )
            for marker in markers
        )
        metadata = {
            "kind": "speech",
            "segment_id": segment_id,
            "speech_hash": entry["speech_hash"],
            "synthesis_key": entry["synthesis_key"],
            "audio_sha256": entry["audio_sha256"],
            "operations": _operation_payload(operations),
            "markers": [dict(marker) for marker in markers],
        }
        if entry.get("engine_metadata"):
            metadata["engine_metadata"] = dict(entry["engine_metadata"])
        if entry.get("warnings"):
            metadata["warnings"] = list(entry["warnings"])
        if entry.get("lowering_diagnostics"):
            metadata["lowering_diagnostics"] = [
                item.to_dict() if hasattr(item, "to_dict") else dict(item)
                for item in entry["lowering_diagnostics"]
            ]
        if entry.get("plan_unit_id") is not None:
            metadata["plan_unit_id"] = entry["plan_unit_id"]
        metadata["directives"] = segment.directives.to_dict()
        if scope_id != "document":
            metadata["scope_id"] = scope_id
        items.append(
            AudioClip(
                id=qualified_id,
                source=source,
                anchors=anchors,
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
            "markers": [dict(marker) for marker in markers],
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
    if transition_policies:
        policy_payload["prosody_transitions"] = {
            scope_id: transition_policies[scope_id] for scope_id in sorted(transition_policies)
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
        schema_version=2,
        output=OutputPolicy(
            sample_rate=sample_rate,
            channels=1,
            loudness=LoudnessPolicy(target_lufs, true_peak_ceiling_dbtp, peak_policy),
            clip_policy=clip_policy,
        ),
        producer={"readio": "project" if project is not None else "readio"},
        source={
            "project_id": project.manifest.project_id if project else None,
            "composition_id": composition_id(identity_payload),
        },
    )
    return job, {
        "identity_payload": identity_payload,
        "composition_id": composition_id(identity_payload),
        "layout": layout,
        "warnings": warnings,
    }


def build_audio_job(
    project: Project,
    *,
    target_lufs: float | None = None,
    true_peak_ceiling_dbtp: float | None = -1.0,
    peak_policy: str = "reduce_gain",
    clip_policy: str = "clamp",
    output_sample_rate: int | None = None,
) -> tuple[AudioJob, dict[str, Any]]:
    scoped_plans = tuple(
        (scope, load_scope_plan(project, scope)) for scope in project.load_plan_index().scopes
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
                "title": (document_scope.title if document_scope is not None else scope.title),
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
            (segment, entries[(scope.id, str(segment.id))]) for segment in plan.segments
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
        output_sample_rate=output_sample_rate,
    )


def _write_composition_result(
    project: Project, job: AudioJob, identity: Mapping[str, Any], result: Any
) -> dict[str, Any]:
    audiojob_path = project.paths["composition_audiojob"]
    audiojob_manifest = Path(job.save(audiojob_path))
    master = project.paths["composition_master"]
    temporary = master.with_name(f".{master.name}.tmp")
    sf.write(temporary, result.audio, result.sample_rate, subtype="PCM_16", format="WAV")
    temporary.replace(master)
    chapter_metadata = identity.get("identity_payload", {}).get("chapters", [])
    item_ranges = {item.item_id: (item.start_sample, item.end_sample) for item in result.items}
    layout_items = identity.get("layout", [])
    timeline_chapters = []
    previous_end = 0
    for chapter in chapter_metadata:
        scope_id = str(chapter.get("scope_id", ""))
        ranges = [
            item_ranges[layout_item["id"]]
            for layout_item in layout_items
            if layout_item.get("scope_id") == scope_id and layout_item.get("id") in item_ranges
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
            "schema_version": 2,
            "composition_id": identity["composition_id"],
            "identity_payload": identity["identity_payload"],
            "warnings": list(identity.get("warnings", [])),
            "synthesis_trace_sha256": (
                hash_file(project.paths["synthesis_trace"])
                if project.paths["synthesis_trace"].is_file()
                else None
            ),
            "synthesis_profile_id": read_json(project.paths["synthesis_profile"]).get("profile_id"),
            "audiojob_sha256": hash_file(audiojob_manifest),
            "master_sha256": master_sha,
            "timeline_sha256": hash_file(project.paths["composition_timeline"]),
            "sample_rate": result.sample_rate,
            "frames": len(result.audio),
            "loudness": {
                "integrated_lufs_before": result.loudness.before.integrated_lufs
                if result.loudness
                else None,
                "integrated_lufs_after": result.loudness.after.integrated_lufs
                if result.loudness
                else None,
                "gain_db": result.loudness.applied_gain_db if result.loudness else 0.0,
            },
        },
    )
    return {
        "composition_id": identity["composition_id"],
        "master": master,
        "sample_rate": result.sample_rate,
        "frames": len(result.audio),
        "items": len(result.items),
        "warnings": list(identity.get("warnings", [])),
    }


def compose_project(
    project: Project,
    *,
    target_lufs: float | None = None,
    true_peak_ceiling_dbtp: float | None = -1.0,
    peak_policy: str = "reduce_gain",
    clip_policy: str = "clamp",
    output_sample_rate: int | None = None,
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
            output_sample_rate=output_sample_rate,
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
    synthesis_profile: Mapping[str, Any] | None = None,
    plans: Any | None = None,
    scope_metadata: tuple[Mapping[str, Any], ...] = (),
    target_lufs: float | None = None,
    true_peak_ceiling_dbtp: float | None = -1.0,
    peak_policy: str = "reduce_gain",
    clip_policy: str = "clamp",
    output_sample_rate: int | None = None,
    output: Path | None = None,
    on_progress: CompositionProgressCallback | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    artifact_list = tuple(artifacts)
    if not artifact_list:
        raise ValueError("preview selected no synthesized segments")
    plan_pairs = (
        tuple(plans) if plans is not None else (("document", plan),) if plan is not None else ()
    )
    if not plan_pairs:
        clips = []
        for artifact in artifact_list:
            scope_id = str(getattr(artifact, "scope_id", "document"))
            segment_id = artifact.segment_id
            qualified_id = segment_id if scope_id == "document" else f"{scope_id}:{segment_id}"
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
            schema_version=2,
            output=OutputPolicy(
                sample_rate=output_sample_rate or clips[0].source.sample_rate,
                channels=1,
                loudness=LoudnessPolicy(target_lufs, true_peak_ceiling_dbtp, peak_policy),
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
            markers_by_segment = _markers_by_segment(scope_plan, str(scope_id))
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
                    "word_timings": tuple(artifact.word_timings),
                    "markers": markers_by_segment[str(segment.id)],
                    "frames": artifact.frames,
                    "speech_hash": artifact.speech_hash or artifact.content_hash,
                    "synthesis_key": artifact.synthesis_key,
                    "composition": dict(composition or {}),
                    "prosody_transitions": (
                        scope_plan.document_metadata.get("prosody_transitions")
                        if isinstance(getattr(scope_plan, "document_metadata", None), Mapping)
                        else None
                    ),
                    "voice_identity": _segment_voice_identity(
                        synthesis_profile, scope_plan, str(scope_id), segment
                    ),
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
            output_sample_rate=output_sample_rate,
        )
    result = Composer().compose(job, on_progress=on_progress)
    if output is not None:
        if on_phase is not None:
            on_phase("Writing preview artifacts")
        output.parent.mkdir(parents=True, exist_ok=True)
        sf.write(output, result.audio, result.sample_rate, subtype="PCM_16", format="WAV")
    return {
        "sample_rate": result.sample_rate,
        "frames": len(result.audio),
        "items": len(result.items),
        "output": output,
        "composition_id": identity["composition_id"],
        "warnings": list(identity.get("warnings", [])),
    }


__all__ = [
    "build_audio_job",
    "compose_artifacts",
    "compose_project",
    "composition_id",
    "seconds_to_frames",
]
