"""Composition of canonical project speech artifacts without TTS access."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from pathlib import Path
from typing import Any, Literal

import numpy as np
from audiocompose import (
    AudioAnchor,
    AudioBufferSource,
    AudioClip,
    AudioFileSource,
    AudioJob,
    Composer,
    FadeIn,
    FadeOut,
    Gain,
    LoudnessPolicy,
    OutputPolicy,
    PitchShift,
    RatePitchEnvelope,
    Silence,
    Tempo,
)
from audiocompose.integrations.voicerender import PausePolicy, materialize_voicerender_job
from voicerender import read_render_manifest

from ..audioio import probe_audio, read_audio, write_pcm16_wav
from ..errors import InputError
from ..jsonutil import JsonValue
from ..plan import DEFAULT_MASTERING_PROFILE, MasteringProfile, resolve_mastering_policy
from ..planning.policy import PlanningPolicy
from ..project import (
    Project,
    atomic_write_bytes,
    atomic_write_json,
    canonical_json,
    hash_file,
    project_lock,
    read_json,
)
from ..stages.speech_identity import (
    segment_route_profile,
    segment_speech_hash,
    segment_synthesis_key,
)
from .planning import load_scope_plan

_LOGGER = logging.getLogger(__name__)


def composition_id(payload: Mapping[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(payload)).hexdigest()}"


def seconds_to_frames(seconds: float, sample_rate: int) -> int:
    """Use the one composition rounding rule for layout and silence audio."""
    return round(float(seconds) * sample_rate)


@dataclass(frozen=True, slots=True)
class CompositionOutcome:
    """Readio-owned composition result detached from AudioCompose types."""

    audio: np.ndarray
    sample_rate: int
    items: tuple[Mapping[str, JsonValue], ...] = ()
    markers: tuple[Mapping[str, JsonValue], ...] = ()
    spans: tuple[Mapping[str, JsonValue], ...] = ()
    loudness: Mapping[str, JsonValue] | None = None

    @property
    def sample_count(self) -> int:
        return int(self.audio.shape[0])


@dataclass(frozen=True, slots=True)
class CompositionProgress:
    """Readio-owned progress event translated at the composition boundary."""

    kind: str
    item_kind: str | None = None
    item_id: str | None = None
    details: Mapping[str, JsonValue] = field(default_factory=dict)


CompositionProgressCallback = Callable[[CompositionProgress], None]


def _artifact_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {"value": value}


def _progress_adapter(
    callback: CompositionProgressCallback | None,
) -> Callable[[Any], None] | None:
    if callback is None:
        return None

    def adapt(event: Any) -> None:
        details = dict(getattr(event, "details", {}) or {})
        item_metadata = dict(getattr(event, "item_metadata", {}) or {})
        if item_metadata:
            details["item_metadata"] = item_metadata
        callback(
            CompositionProgress(
                kind=str(getattr(event, "kind", "")),
                item_kind=getattr(event, "item_kind", None),
                item_id=getattr(event, "item_id", None),
                details=details,
            )
        )

    return adapt


def _outcome(result: Any) -> CompositionOutcome:
    return CompositionOutcome(
        audio=np.asarray(result.audio),
        sample_rate=int(result.sample_rate),
        items=tuple(_artifact_payload(item) for item in result.items),
        markers=tuple(_artifact_payload(marker) for marker in result.markers),
        spans=tuple(_artifact_payload(span) for span in result.spans),
        loudness=_artifact_payload(result.loudness) if result.loudness is not None else None,
    )


def compose_job(
    job: AudioJob,
    *,
    on_progress: CompositionProgressCallback | None = None,
) -> CompositionOutcome:
    """Execute one built layout and return Readio-owned composition data."""
    result = Composer().compose(job, on_progress=_progress_adapter(on_progress))
    return _outcome(result)


def compose_segment(
    job: AudioJob,
    *,
    on_progress: CompositionProgressCallback | None = None,
) -> CompositionOutcome:
    """Compose one prepared segment job for interactive playback."""
    return compose_job(job, on_progress=on_progress)


def compose_layout(
    job: AudioJob,
    *,
    on_progress: CompositionProgressCallback | None = None,
) -> CompositionOutcome:
    """Compose a full-program layout job."""
    return compose_job(job, on_progress=on_progress)


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
    segment_routes = profile_payload.get("segment_routes", {})
    scope_routes = segment_routes.get(scope_id, {}) if isinstance(segment_routes, Mapping) else {}
    route_key = scope_routes.get(str(segment.id)) if isinstance(scope_routes, Mapping) else None
    if isinstance(route_key, str):
        return f"route:{route_key}"
    if reference is not None:
        scoped_bindings = canonical.get("bindings_by_scope", {})
        if isinstance(scoped_bindings, Mapping):
            bindings = scoped_bindings.get(scope_id, {})
            if isinstance(bindings, Mapping) and isinstance(bindings.get(reference), str):
                return f"target:{bindings[reference]}"
        namespace = profile_payload.get("voice_binding_namespace")
        metadata = getattr(plan, "document_metadata", {})
        document_bindings = (
            metadata.get("voice_bindings", {}) if isinstance(metadata, Mapping) else {}
        )
        if isinstance(document_bindings, Mapping):
            if namespace is not None and isinstance(document_bindings.get(namespace), Mapping):
                target = document_bindings[namespace].get(reference)
            else:
                target = document_bindings.get(reference)
                if target is None and len(document_bindings) == 1:
                    only_bindings = next(iter(document_bindings.values()))
                    target = (
                        only_bindings.get(reference) if isinstance(only_bindings, Mapping) else None
                    )
            if isinstance(target, str):
                return f"target:{target}"
        role_targets = profile_payload.get("project_role_targets", {})
        bindings = role_targets.get("bindings", {}) if isinstance(role_targets, Mapping) else {}
        target = bindings.get(reference) if isinstance(bindings, Mapping) else None
        if isinstance(target, Mapping):
            voice = target.get("target_id") or target.get("voice")
            if isinstance(voice, str):
                return f"target:{voice}"
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


def _duration_seconds(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise InputError(f"{field} must be a duration, not a boolean")
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        match = re.fullmatch(
            r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(ms|s)?\s*",
            value,
            re.IGNORECASE,
        )
        if match is None:
            raise InputError(f"{field} must be a number of seconds or use ms/s syntax")
        seconds = float(match.group(1))
        if match.group(2) and match.group(2).lower() == "ms":
            seconds /= 1000.0
    else:
        raise InputError(f"{field} must be a number or duration string")
    if not math.isfinite(seconds) or seconds < 0:
        raise InputError(f"{field} must be finite and non-negative")
    return seconds


def _transition_seconds(policy: Mapping[str, Any], field: str) -> float:
    value = policy.get(field)
    return _duration_seconds(value, field) if value is not None else 0.0


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


def _valid_audio(path: Path, expected_sha: str | None = None) -> tuple[int, int, int, str] | None:
    if not path.is_file():
        return None
    try:
        info = probe_audio(path)
        digest = hash_file(path)
    except (OSError, RuntimeError, ValueError):
        return None
    if expected_sha is not None and digest != expected_sha:
        return None
    if info.frames <= 0 or info.sample_rate <= 0:
        return None
    return int(info.sample_rate), int(info.channels), int(info.frames), digest

def _cache_entries(
    project: Project, plan: Any, scope_id: str
) -> dict[tuple[str, str], dict[str, Any]]:
    profile = read_json(project.paths["synthesis_profile"])
    canonical = profile.get("canonical")
    profile_id = str(profile.get("profile_id", ""))
    if not isinstance(canonical, dict) or not profile_id:
        raise ValueError("synthesis profile is missing canonical identity")
    entries: dict[tuple[str, str], dict[str, Any]] = {}
    cache_dir = project.state_root / "synthesis" / "cache"
    markers_by_segment = _markers_by_segment(plan, scope_id)
    for segment in plan.segments:
        route_identity = segment_route_profile(profile, scope_id, str(segment.id))
        if route_identity is None:
            raise ValueError(f"synthesis route is missing for {scope_id}:{segment.id}")
        route_profile, route_profile_id = route_identity
        speech_hash = segment_speech_hash(plan, segment, route_profile)
        key = segment_synthesis_key(speech_hash, route_profile_id)
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
            or sidecar.get("profile_id") != route_profile_id
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
            "profile_id": route_profile_id,
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
        path = project.state_root / "composition" / "parts" / f"silence-{sample_rate}-{frames}.wav"
        if not path.is_file() or probe_audio(path).frames != frames:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.tmp")
            write_pcm16_wav(temporary, np.zeros(frames, dtype=np.float32), sample_rate)
            temporary.replace(path)
        digest = hash_file(path)
        return AudioFileSource(
            path, expected_sha256=digest, sample_rate=sample_rate, frames=frames
        ), digest
    audio = np.zeros(frames, dtype=np.float32)
    return AudioBufferSource(audio, sample_rate), hashlib.sha256(audio.tobytes()).hexdigest()


class LayoutBuilder:
    """Stateful incremental audio layout preserving full-render semantics."""

    def __init__(
        self,
        project: Project | None,
        *,
        sample_rate: int,
        target_lufs: float | None,
        true_peak_ceiling_dbtp: float | None,
        peak_policy: Literal["reduce_gain", "error"],
        clip_policy: str,
        mastering: MasteringProfile = DEFAULT_MASTERING_PROFILE,
        composition: Mapping[str, Any] | None = None,
        scope_metadata: tuple[Mapping[str, Any], ...] = (),
    ) -> None:
        self.project = project
        self.sample_rate = int(sample_rate)
        self.clip_policy = clip_policy
        self.composition = composition
        self.scope_metadata = scope_metadata
        self.mastering_policy = resolve_mastering_policy(
            mastering,
            target_lufs=target_lufs,
            true_peak_ceiling_dbtp=true_peak_ceiling_dbtp,
            peak_policy=peak_policy,
        )
        self.layout: list[dict[str, Any]] = []
        self.seen_events: set[str] = set()
        self.transition_policies: dict[str, dict[str, Any]] = {}
        self.warnings: list[str] = []
        self.warned_volume_scopes: set[str] = set()
        self.previous_scope_id: str | None = None
        self.previous_prosody: tuple[float, float, str] | None = None

    def _append_silence(
        self,
        segment: Any,
        entry: Mapping[str, Any],
        side: str,
        pause: Any,
        clips: list[AudioClip],
    ) -> None:
        seconds, event_ids = _pause_seconds(pause)
        scope_id = str(entry.get("scope_id", "document"))
        scoped_events = tuple(
            f"{scope_id}:{event_id}" if scope_id != "document" else event_id
            for event_id in event_ids
        )
        unique_events = tuple(
            event_id for event_id in scoped_events if event_id not in self.seen_events
        )
        if unique_events:
            self.seen_events.update(unique_events)
        if seconds <= 0 or (event_ids and not unique_events):
            return
        frames = seconds_to_frames(seconds, self.sample_rate)
        if frames <= 0:
            return
        source, silence_hash = _write_silence(self.project, self.sample_rate, frames)
        segment_id = str(segment.id)
        qualified_id = segment_id if scope_id == "document" else f"{scope_id}:{segment_id}"
        item_id = f"silence-{qualified_id}-{side}"
        metadata: dict[str, Any] = {
            "kind": "silence",
            "segment_id": segment_id,
            "side": side,
            "event_ids": list(unique_events),
            "frames": frames,
        }
        if scope_id != "document":
            metadata["scope_id"] = scope_id
        clips.append(AudioClip(id=item_id, source=source, metadata=metadata))
        layout_item: dict[str, Any] = {
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
        self.layout.append(layout_item)

    def append(self, segment: Any, entry: Mapping[str, Any]) -> tuple[AudioClip, ...]:
        """Append one segment and return only its newly emitted clips."""
        clips: list[AudioClip] = []
        scope_id = str(entry.get("scope_id", "document"))
        if scope_id != self.previous_scope_id:
            self.previous_scope_id = scope_id
            self.previous_prosody = None
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
            self.transition_policies[scope_id] = transition_policy
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
                and scope_id not in self.warned_volume_scopes
            ):
                warning = (
                    "AudioJob v2 does not support continuous volume transitions; "
                    f"using static volume for scope {scope_id!r}."
                )
                self.warnings.append(warning)
                self.warned_volume_scopes.add(scope_id)
                _LOGGER.warning("%s", warning)

        segment_id = str(segment.id)
        qualified_id = segment_id if scope_id == "document" else f"{scope_id}:{segment_id}"
        self._append_silence(
            segment, entry, "before", getattr(segment, "pause_before", None), clips
        )
        segment_composition = self.composition or entry.get("composition", {})
        rate = _effective_rate_factor(segment, segment_composition)
        pitch = _effective_pitch_semitones(segment, segment_composition)
        voice_identity = entry.get("voice_identity") or _segment_voice_identity(
            None, None, scope_id, segment
        )
        envelope = None
        if transition_enabled and self.previous_prosody is not None:
            previous_rate, previous_pitch, previous_voice = self.previous_prosody
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
        self.previous_prosody = (rate, pitch, voice_identity)
        if self.project is None:
            part = None
        elif scope_id == "document":
            part = self.project.state_root / "composition" / "parts" / f"{segment_id}.wav"
        else:
            part = (
                self.project.state_root / "composition" / "parts" / scope_id / f"{segment_id}.wav"
            )
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
            audio, source_rate = read_audio(source_path)
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
        metadata: dict[str, Any] = {
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
        clips.append(
            AudioClip(
                id=qualified_id,
                source=source,
                anchors=anchors,
                operations=operations,
                metadata=metadata,
            )
        )
        layout_item: dict[str, Any] = {
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
        self.layout.append(layout_item)
        self._append_silence(segment, entry, "after", getattr(segment, "pause_after", None), clips)
        return tuple(clips)

    def build_job(self, clips: Sequence[AudioClip]) -> tuple[AudioJob, dict[str, Any]]:
        """Build a job from the provided clips and current layout metadata."""
        if not clips:
            raise ValueError("composition selected no synthesized segments")
        target_lufs = self.mastering_policy.target_lufs
        true_peak_ceiling_dbtp = self.mastering_policy.true_peak_ceiling_dbtp
        peak_policy = self.mastering_policy.peak_policy
        policy_payload: dict[str, Any] = {
            "sample_rate": self.sample_rate,
            "channels": 1,
            "loudness": {
                "profile": self.mastering_policy.profile,
                "target_lufs": target_lufs,
                "true_peak_ceiling_dbtp": true_peak_ceiling_dbtp,
                "peak_policy": peak_policy,
                "collect_metrics": self.mastering_policy.collect_metrics,
            },
            "clip_policy": self.clip_policy,
        }
        if self.transition_policies:
            policy_payload["prosody_transitions"] = {
                scope_id: self.transition_policies[scope_id]
                for scope_id in sorted(self.transition_policies)
            }
        identity_payload: dict[str, Any] = {
            "schema": "readio.composition.v2",
            "items": list(self.layout),
            **policy_payload,
        }
        if len(self.scope_metadata) > 1 or any(
            item.get("kind") == "chapter" for item in self.scope_metadata
        ):
            identity_payload["schema"] = "readio.composition.v3"
            identity_payload["scopes"] = [dict(item) for item in self.scope_metadata]
            identity_payload["chapters"] = [
                dict(item) for item in self.scope_metadata if item.get("kind") == "chapter"
            ]
        identity = composition_id(identity_payload)
        job = AudioJob(
            items=tuple(clips),
            schema_version=2,
            output=OutputPolicy(
                sample_rate=self.sample_rate,
                channels=1,
                loudness=LoudnessPolicy(
                    target_lufs,
                    true_peak_ceiling_dbtp,
                    peak_policy,
                    collect_metrics=self.mastering_policy.collect_metrics,
                ),
                clip_policy=self.clip_policy,
            ),
            producer={"readio": "project" if self.project is not None else "readio"},
            source={
                "project_id": self.project.manifest.project_id if self.project else None,
                "composition_id": identity,
            },
        )
        return job, {
            "identity_payload": identity_payload,
            "composition_id": identity,
            "layout": list(self.layout),
            "warnings": list(self.warnings),
        }


def _build_layout(
    project: Project | None,
    segments: Sequence[tuple[Any, Mapping[str, Any]]],
    *,
    target_lufs: float | None,
    true_peak_ceiling_dbtp: float | None,
    peak_policy: Literal["reduce_gain", "error"],
    clip_policy: str,
    mastering: MasteringProfile = DEFAULT_MASTERING_PROFILE,
    composition: Mapping[str, Any] | None = None,
    scope_metadata: tuple[Mapping[str, Any], ...] = (),
    output_sample_rate: int | None = None,
) -> tuple[AudioJob, dict[str, Any]]:
    if not segments:
        raise ValueError("composition selected no synthesized segments")
    sample_rate = int(output_sample_rate or segments[0][1]["sample_rate"])
    builder = LayoutBuilder(
        project,
        sample_rate=sample_rate,
        target_lufs=target_lufs,
        true_peak_ceiling_dbtp=true_peak_ceiling_dbtp,
        peak_policy=peak_policy,
        clip_policy=clip_policy,
        mastering=mastering,
        composition=composition,
        scope_metadata=scope_metadata,
    )
    clips = tuple(clip for segment, entry in segments for clip in builder.append(segment, entry))
    return builder.build_job(clips)


def _pause_policy_document() -> dict[str, Any]:
    planning = PlanningPolicy()

    def duration(seconds: float) -> str:
        return f"{seconds * 1000:g}ms"

    return {
        "schema": "audiocompose.pause-policy.v1",
        "merge": "max",
        "default": {
            "none": "0ms",
            "x-weak": duration(planning.pause_weak / 2),
            "weak": duration(planning.pause_weak),
            "medium": duration(planning.pause_clause),
            "strong": duration(planning.pause_sentence),
            "x-strong": duration(planning.pause_paragraph),
            "clause": duration(planning.pause_clause),
            "sentence": duration(planning.pause_sentence),
            "paragraph": duration(planning.pause_paragraph),
            "parenthetical": duration(planning.pause_parenthetical),
            "voice_change": duration(planning.pause_voice_change),
        },
    }


def _pause_policy_toml(document: Mapping[str, Any]) -> str:
    lines = [
        f"schema = {json.dumps(document['schema'])}",
        f"merge = {json.dumps(document['merge'])}",
        "",
        "[default]",
    ]
    lines.extend(
        f"{name} = {json.dumps(value)}"
        for name, value in sorted(document["default"].items())
    )
    return "\n".join(lines) + "\n"


def _output_policy(
    *,
    sample_rate: int,
    mastering: MasteringProfile,
    target_lufs: float | None,
    true_peak_ceiling_dbtp: float | None,
    peak_policy: Literal["reduce_gain", "error"],
    clip_policy: str,
) -> OutputPolicy:
    resolved = resolve_mastering_policy(
        mastering,
        target_lufs=target_lufs,
        true_peak_ceiling_dbtp=true_peak_ceiling_dbtp,
        peak_policy=peak_policy,
    )
    return OutputPolicy(
        sample_rate=sample_rate,
        channels=1,
        loudness=LoudnessPolicy(
            target_lufs=resolved.target_lufs,
            true_peak_ceiling_dbtp=resolved.true_peak_ceiling_dbtp,
            peak_policy=resolved.peak_policy,
            collect_metrics=resolved.collect_metrics,
        ),
        clip_policy=clip_policy,
    )


def _locator_order(plan: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    order = 0
    for flow_index, flow in enumerate(plan.flow):
        for segment_index, _segment in enumerate(flow.segments):
            result[f"f{flow_index:06d}.s{segment_index:03d}"] = order
            order += 1
    return result


def _segment_at_locator(plan: Any, locator: str) -> Any:
    flow_index, segment_index = map(int, locator[1:].split(".s"))
    return plan.flow[flow_index].segments[segment_index]


def _scoped_audio_item(item: Any, scope_id: str) -> Any:
    metadata = {**dict(item.metadata), "readio.scope_id": scope_id}
    if isinstance(item, AudioClip):
        return replace(
            item,
            id=f"{scope_id}:{item.id}",
            anchors=tuple(replace(anchor, id=f"{scope_id}:{anchor.id}") for anchor in item.anchors),
            spans=tuple(
                replace(span, id=f"{scope_id}:{span.id}") if span.id is not None else span
                for span in item.spans
            ),
            metadata=metadata,
        )
    return replace(item, id=f"{scope_id}:{item.id}", metadata=metadata)


def _pause_boundary(
    policy: PausePolicy,
    plan: Any,
    scope_id: str,
    left_locator: str,
    right_locator: str,
) -> Silence | None:
    left = _segment_at_locator(plan, left_locator)
    right = _segment_at_locator(plan, right_locator)
    left_role = _segment_voice_reference(left) or "narrator"
    right_role = _segment_voice_reference(right) or "narrator"

    def resolve(intent: Any, role: str, edge: str) -> float:
        value = intent.to_dict() if hasattr(intent, "to_dict") else intent
        return policy.resolve(
            value,
            voice=role,
            where=f"{scope_id}:{left_locator}->{right_locator}:{edge}",
        )

    seconds = max(
        resolve(left.pause_after, left_role, "pause_after"),
        resolve(right.pause_before, right_role, "pause_before"),
    )
    if seconds <= 0:
        return None
    return Silence(
        id=f"{scope_id}:pause:{left_locator}->{right_locator}",
        seconds=seconds,
        metadata={
            "kind": "pause",
            "readio.scope_id": scope_id,
            "utterplan.plan_id": plan.plan_id,
            "utterplan.left_locator": left_locator,
            "utterplan.right_locator": right_locator,
            "audiocompose.pause_policy_id": policy.policy_id,
        },
    )


def build_audio_job(
    project: Project,
    *,
    mastering: MasteringProfile = DEFAULT_MASTERING_PROFILE,
    target_lufs: float | None = None,
    true_peak_ceiling_dbtp: float | None = None,
    peak_policy: Literal["reduce_gain", "error"] = "reduce_gain",
    clip_policy: str = "clamp",
    output_sample_rate: int | None = None,
) -> tuple[AudioJob, dict[str, Any]]:
    """Materialize project render manifests into one producer-neutral AudioJob."""
    profile = read_json(project.paths["synthesis_profile"])
    manifest_refs = profile.get("manifest_refs")
    if not isinstance(manifest_refs, list) or not manifest_refs:
        raise ValueError("active synthesis profile has no VoiceRender manifests")
    scoped_plans = tuple(
        (scope, load_scope_plan(project, scope)) for scope in project.load_plan_index().scopes
    )
    plans_by_scope = {scope.id: (scope, plan) for scope, plan in scoped_plans}
    plan_order = {scope.id: index for index, (scope, _plan) in enumerate(scoped_plans)}
    pause_document = _pause_policy_document()
    pause_policy = PausePolicy.from_mapping(pause_document)
    ordered_refs = sorted(
        manifest_refs,
        key=lambda item: (
            plan_order.get(str(item.get("scope_id", "")), len(plan_order)),
            int(item.get("run_index", 0)),
        ),
    )
    manifest_documents: dict[str, Mapping[str, Any]] = {}
    first_sample_rate: int | None = None
    scope_metadata = []
    for scope, plan in scoped_plans:
        document_scope = next(
            (item for item in project.document_scopes() if item.id == scope.id), None
        )
        scope_metadata.append(
            {
                "scope_id": scope.id,
                "kind": document_scope.kind if document_scope is not None else scope.kind,
                "title": document_scope.title if document_scope is not None else scope.title,
                "source_number": (
                    document_scope.source_number if document_scope is not None else None
                ),
                "plan_id": plan.plan_id,
                "plan_sha256": hash_file(project.state_root / "plan" / scope.path),
            }
        )
    for reference in ordered_refs:
        if not isinstance(reference, Mapping):
            raise TypeError("synthesis manifest references must be objects")
        scope_id = str(reference.get("scope_id", ""))
        current = plans_by_scope.get(scope_id)
        if current is None:
            raise ValueError(f"synthesis manifest references unknown scope {scope_id!r}")
        scope, _plan = current
        manifest_value = reference.get("path")
        if not isinstance(manifest_value, str):
            raise TypeError("synthesis manifest reference is missing its path")
        manifest_path = project.state_root / manifest_value
        manifest = read_render_manifest(manifest_path)
        manifest_documents[scope_id + "|" + manifest_value] = manifest
        if first_sample_rate is None:
            for entry in manifest.get("segments", []):
                audio = entry.get("audio") if isinstance(entry, Mapping) else None
                if isinstance(audio, Mapping) and isinstance(audio.get("sample_rate"), int):
                    first_sample_rate = audio["sample_rate"]
                    break
    sample_rate = int(output_sample_rate or first_sample_rate or 24000)
    output = _output_policy(
        sample_rate=sample_rate,
        mastering=mastering,
        target_lufs=target_lufs,
        true_peak_ceiling_dbtp=true_peak_ceiling_dbtp,
        peak_policy=peak_policy,
        clip_policy=clip_policy,
    )
    items = []
    plan_refs = []
    selections_by_scope: dict[str, list[tuple[int, str]]] = {}
    for reference in ordered_refs:
        scope_id = str(reference["scope_id"])
        scope, plan = plans_by_scope[scope_id]
        plan_path = project.state_root / "plan" / scope.path
        manifest_path = project.state_root / str(reference["path"])
        job = materialize_voicerender_job(
            plan=plan_path,
            render_manifest=manifest_path,
            pause_policy=pause_policy,
            output_policy=output,
        )
        manifest = manifest_documents[scope_id + "|" + str(reference["path"])]
        selection = manifest.get("selection", {})
        locators = selection.get("locators", []) if isinstance(selection, Mapping) else []
        if not isinstance(locators, list) or not locators:
            raise ValueError(f"VoiceRender manifest for {scope_id!r} has no selected locators")
        order = _locator_order(plan)
        start_locator, end_locator = str(locators[0]), str(locators[-1])
        prior = selections_by_scope.get(scope_id)
        if prior:
            previous_order, previous_locator = prior[-1]
            next_order = order.get(start_locator)
            if next_order == previous_order + 1:
                boundary = _pause_boundary(
                    pause_policy, plan, scope_id, previous_locator, start_locator
                )
                if boundary is not None:
                    items.append(boundary)
        items.extend(_scoped_audio_item(item, scope_id) for item in job.items)
        selections_by_scope.setdefault(scope_id, []).append(
            (order.get(end_locator, -1), end_locator)
        )
        plan_refs.append(
            {
                "scope_id": scope_id,
                "plan_id": plan.plan_id,
                "path": scope.path,
                "sha256": hash_file(plan_path),
            }
        )
    if not items:
        raise ValueError("project plans contain no AudioCompose items")
    source = {
        "readio.project_id": project.manifest.project_id,
        "readio.synthesis_profile_id": profile.get("profile_id"),
        "readio.plan_refs": plan_refs,
        "voicerender.manifest_refs": [dict(reference) for reference in ordered_refs],
        "audiocompose.pause_policy_id": pause_policy.policy_id,
    }
    job = AudioJob(
        items=tuple(items),
        output=output,
        producer={"name": "readio.project-composition"},
        source=source,
    )
    job.validate(verify_sources=True)
    identity = {
        "job_id": job.job_id,
        "pause_policy_id": pause_policy.policy_id,
        "pause_policy": pause_document,
        "output_policy": {
            "sample_rate": output.sample_rate,
            "channels": output.channels,
            "target_lufs": output.loudness.target_lufs,
            "true_peak_ceiling_dbtp": output.loudness.true_peak_ceiling_dbtp,
            "peak_policy": output.loudness.peak_policy,
            "clip_policy": output.clip_policy,
        },
        "scope_metadata": scope_metadata,
        "manifest_refs": [dict(reference) for reference in ordered_refs],
        "plan_refs": plan_refs,
        "mastering_profile": mastering,
    }
    return job, identity



def _loudness_payload(loudness: Mapping[str, Any] | None, profile: str) -> dict[str, Any] | None:
    if loudness is None:
        return None
    before = loudness["before"]
    after = loudness["after"]
    return {
        "profile": profile,
        "integrated_lufs_before": before["integrated_lufs"],
        "integrated_lufs_after": after["integrated_lufs"],
        "sample_peak_dbfs_before": before["sample_peak_dbfs"],
        "sample_peak_dbfs_after": after["sample_peak_dbfs"],
        "true_peak_dbtp_before": before["true_peak_dbtp"],
        "true_peak_dbtp_after": after["true_peak_dbtp"],
        "target_lufs": loudness["target_lufs"],
        "true_peak_ceiling_dbtp": loudness["true_peak_ceiling_dbtp"],
        "requested_gain_db": loudness["requested_gain_db"],
        "applied_gain_db": loudness["applied_gain_db"],
        "gain_db": loudness["applied_gain_db"],
        "target_reached": loudness["target_reached"],
        "peak_policy": loudness["peak_policy"],
        "warning": loudness["warning"],
        "analysis_seconds": loudness["analysis_seconds"],
        "gain_seconds": loudness["gain_seconds"],
        "post_gain_metrics_seconds": loudness["post_gain_metrics_seconds"],
    }


def _write_composition_result(
    project: Project,
    job: AudioJob,
    identity: Mapping[str, Any],
    result: Any,
    *,
    on_phase: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    audiojob_path = project.paths["composition_audiojob"]
    if on_phase is not None:
        on_phase("Writing AudioJob manifest")
    phase_started = time.perf_counter()
    audiojob_manifest = Path(job.save(audiojob_path))
    persisted_job = AudioJob.load(audiojob_manifest)
    job_id = str(persisted_job.job_id)
    if on_phase is not None:
        on_phase(f"AudioJob manifest written in {time.perf_counter() - phase_started:.3f}s")

    composition_root = project.state_root / "composition"
    pause_policy_path = composition_root / "pause-policy.toml"
    atomic_write_bytes(
        pause_policy_path,
        _pause_policy_toml(identity["pause_policy"]).encode("utf-8"),
    )
    master = project.paths["composition_master"]
    temporary = master.with_name(f".{master.name}.tmp")
    if on_phase is not None:
        on_phase("Writing master WAV")
    phase_started = time.perf_counter()
    write_pcm16_wav(temporary, result.audio, result.sample_rate)
    temporary.replace(master)
    if on_phase is not None:
        on_phase(f"Master WAV written in {time.perf_counter() - phase_started:.3f}s")

    if on_phase is not None:
        on_phase("Building and writing composition timeline")
    phase_started = time.perf_counter()
    item_metadata = {item.id: dict(item.metadata) for item in persisted_job.items}
    timeline_items = []
    chapter_ranges: dict[str, list[tuple[int, int]]] = {}
    for item in result.items:
        metadata = item_metadata.get(item["item_id"], {})
        scope_id = str(metadata.get("readio.scope_id", ""))
        start_sample = int(item["start_sample"])
        end_sample = int(item["end_sample"])
        if scope_id:
            chapter_ranges.setdefault(scope_id, []).append((start_sample, end_sample))
        timeline_items.append(
            {
                "id": item["item_id"],
                "kind": item["kind"],
                "scope_id": scope_id or None,
                "start_sample": start_sample,
                "end_sample": end_sample,
                "source_sample_rate": item["source_sample_rate"],
            }
        )
    timeline_chapters = []
    for chapter in identity.get("scope_metadata", []):
        scope_id = str(chapter.get("scope_id", ""))
        ranges = chapter_ranges.get(scope_id, [])
        if not ranges:
            continue
        timeline_chapters.append(
            {
                "scope_id": scope_id,
                "source_number": chapter.get("source_number"),
                "title": chapter.get("title"),
                "start_sample": min(start for start, _end in ranges),
                "end_sample": max(end for _start, end in ranges),
            }
        )
    timeline = {
        "format": "readio.composition-timeline",
        "schema_version": 3,
        "job_id": job_id,
        "composition_id": job_id,
        "sample_rate": result.sample_rate,
        "chapters": timeline_chapters,
        "items": timeline_items,
        "markers": [
            {
                "id": marker["id"],
                "sample_offset": marker["sample_offset"],
                "name": marker["name"],
                "item_id": marker["item_id"],
            }
            for marker in result.markers
        ],
    }
    atomic_write_json(project.paths["composition_timeline"], timeline)
    if on_phase is not None:
        on_phase(f"Composition timeline written in {time.perf_counter() - phase_started:.3f}s")

    if on_phase is not None:
        on_phase("Hashing composition artifacts")
    phase_started = time.perf_counter()
    master_sha = hash_file(master)
    audiojob_sha = hash_file(audiojob_manifest)
    timeline_sha = hash_file(project.paths["composition_timeline"])
    pause_policy_sha = hash_file(pause_policy_path)
    synthesis_trace = project.paths["synthesis_trace"]
    synthesis_trace_sha = hash_file(synthesis_trace) if synthesis_trace.is_file() else None
    if on_phase is not None:
        on_phase(f"Composition artifacts hashed in {time.perf_counter() - phase_started:.3f}s")
    profile = str(identity.get("mastering_profile", DEFAULT_MASTERING_PROFILE))
    loudness_summary = _loudness_payload(result.loudness, profile)
    identity_payload = {
        "job_id": job_id,
        "pause_policy_id": identity["pause_policy_id"],
        "output_policy": identity["output_policy"],
        "manifest_refs": identity["manifest_refs"],
        "plan_refs": identity["plan_refs"],
    }
    if on_phase is not None:
        on_phase("Writing composition state")
    phase_started = time.perf_counter()
    atomic_write_json(
        project.paths["composition_state"],
        {
            "format": "readio.composition-state",
            "schema_version": 3,
            "job_id": job_id,
            "composition_id": job_id,
            "identity_payload": identity_payload,
            "pause_policy_path": pause_policy_path.relative_to(project.state_root).as_posix(),
            "pause_policy_id": identity["pause_policy_id"],
            "pause_policy_sha256": pause_policy_sha,
            "output_policy": identity["output_policy"],
            "mastering_profile": profile,
            "manifest_refs": identity["manifest_refs"],
            "plan_refs": identity["plan_refs"],
            "warnings": [],
            "synthesis_trace_sha256": synthesis_trace_sha,
            "synthesis_profile_id": read_json(project.paths["synthesis_profile"]).get("profile_id"),
            "audiojob_manifest_path": audiojob_manifest.relative_to(project.state_root).as_posix(),
            "audiojob_sha256": audiojob_sha,
            "master_sha256": master_sha,
            "timeline_sha256": timeline_sha,
            "sample_rate": result.sample_rate,
            "frames": len(result.audio),
            "loudness": loudness_summary,
        },
    )
    if on_phase is not None:
        on_phase(f"Composition state written in {time.perf_counter() - phase_started:.3f}s")
    return {
        "job_id": job_id,
        "composition_id": job_id,
        "mastering_profile": profile,
        "master": master,
        "sample_rate": result.sample_rate,
        "frames": len(result.audio),
        "items": len(result.items),
        "warnings": [],
        "loudness": loudness_summary,
    }


def compose_project(
    project: Project,
    *,
    mastering: MasteringProfile = DEFAULT_MASTERING_PROFILE,
    target_lufs: float | None = None,
    true_peak_ceiling_dbtp: float | None = None,
    peak_policy: Literal["reduce_gain", "error"] = "reduce_gain",
    clip_policy: str = "clamp",
    output_sample_rate: int | None = None,
    on_progress: CompositionProgressCallback | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    with project_lock(project, operation="compose"):
        if on_phase is not None:
            on_phase("Preparing composition")
        phase_started = time.perf_counter()
        job, identity = build_audio_job(
            project,
            mastering=mastering,
            target_lufs=target_lufs,
            true_peak_ceiling_dbtp=true_peak_ceiling_dbtp,
            peak_policy=peak_policy,
            clip_policy=clip_policy,
            output_sample_rate=output_sample_rate,
        )
        if on_phase is not None:
            on_phase(f"Composition layout prepared in {time.perf_counter() - phase_started:.3f}s")
        result = compose_job(job, on_progress=on_progress)
        if on_phase is not None:
            on_phase("Writing composition artifacts")
        return _write_composition_result(project, job, identity, result, on_phase=on_phase)







def composition_is_current(
    project: Project,
    *,
    mastering: MasteringProfile = DEFAULT_MASTERING_PROFILE,
    target_lufs: float | None = None,
    true_peak_ceiling_dbtp: float | None = None,
    peak_policy: Literal["reduce_gain", "error"] = "reduce_gain",
    clip_policy: str = "clamp",
    output_sample_rate: int | None = None,
) -> bool:
    """Check persisted composition artifacts against current public inputs."""
    try:
        state = read_json(project.paths["composition_state"])
        profile = read_json(project.paths["synthesis_profile"])
        if not isinstance(state, Mapping) or not isinstance(profile, Mapping):
            return False
        manifest_refs = profile.get("manifest_refs")
        if not isinstance(manifest_refs, list) or state.get("manifest_refs") != manifest_refs:
            return False
        manifest_path_value = state.get("audiojob_manifest_path")
        if not isinstance(manifest_path_value, str):
            return False
        audiojob_manifest = project.state_root / manifest_path_value
        if not audiojob_manifest.is_file():
            return False
        job = AudioJob.load(audiojob_manifest)
        job_id = job.job_id
        if not isinstance(job_id, str) or state.get("job_id") != job_id:
            return False
        if state.get("composition_id") != job_id:
            return False
        if job.source.get("readio.synthesis_profile_id") != profile.get("profile_id"):
            return False
        if job.source.get("voicerender.manifest_refs") != manifest_refs:
            return False
        expected_pause_policy = PausePolicy.from_mapping(_pause_policy_document())
        if (
            state.get("pause_policy_id") != expected_pause_policy.policy_id
            or job.source.get("audiocompose.pause_policy_id") != expected_pause_policy.policy_id
        ):
            return False
        pause_policy_path = project.state_root / "composition" / "pause-policy.toml"
        if (
            state.get("pause_policy_path") != "composition/pause-policy.toml"
            or not pause_policy_path.is_file()
            or hash_file(pause_policy_path) != state.get("pause_policy_sha256")
            or PausePolicy.load(pause_policy_path).policy_id != expected_pause_policy.policy_id
        ):
            return False
        expected_output = _output_policy(
            sample_rate=int(output_sample_rate or job.output.sample_rate),
            mastering=mastering,
            target_lufs=target_lufs,
            true_peak_ceiling_dbtp=true_peak_ceiling_dbtp,
            peak_policy=peak_policy,
            clip_policy=clip_policy,
        )
        if job.output != expected_output:
            return False
        expected_output_payload = {
            "sample_rate": expected_output.sample_rate,
            "channels": expected_output.channels,
            "target_lufs": expected_output.loudness.target_lufs,
            "true_peak_ceiling_dbtp": expected_output.loudness.true_peak_ceiling_dbtp,
            "peak_policy": expected_output.loudness.peak_policy,
            "clip_policy": expected_output.clip_policy,
        }
        if state.get("output_policy") != expected_output_payload:
            return False
        plan_refs = []
        for scope in project.load_plan_index().scopes:
            plan = load_scope_plan(project, scope)
            plan_path = project.state_root / "plan" / scope.path
            plan_refs.append(
                {
                    "scope_id": scope.id,
                    "plan_id": plan.plan_id,
                    "path": scope.path,
                    "sha256": hash_file(plan_path),
                }
            )
        if state.get("plan_refs") != plan_refs or job.source.get("readio.plan_refs") != plan_refs:
            return False
        if hash_file(audiojob_manifest) != state.get("audiojob_sha256"):
            return False
        if not project.paths["composition_master"].is_file() or hash_file(
            project.paths["composition_master"]
        ) != state.get("master_sha256"):
            return False
        if not project.paths["composition_timeline"].is_file() or hash_file(
            project.paths["composition_timeline"]
        ) != state.get("timeline_sha256"):
            return False
        timeline = read_json(project.paths["composition_timeline"])
        if not isinstance(timeline, Mapping) or timeline.get("job_id") != job_id:
            return False
        synthesis_trace = project.paths["synthesis_trace"]
        current_trace_hash = hash_file(synthesis_trace) if synthesis_trace.is_file() else None
        return state.get("synthesis_trace_sha256") == current_trace_hash and state.get(
            "synthesis_profile_id"
        ) == profile.get("profile_id")
    except (OSError, TypeError, ValueError, KeyError):
        return False


__all__ = [
    "build_audio_job",
    "compose_project",
    "composition_id",
    "composition_is_current",
    "seconds_to_frames",
]
