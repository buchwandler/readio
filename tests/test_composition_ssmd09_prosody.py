from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from audiocompose import AudioJob, Composer, Gain, PitchShift, RatePitchEnvelope, Tempo
from utterplan import Marker

from readio.document import document_from_text
from readio.engines.base import SpeechWordTiming
from readio.errors import InputError
from readio.planning.compiler import compile_semantic_plan
from readio.planning.policy import PlanningPolicy
from readio.stages.composition import (
    _build_layout,
    _markers_by_segment,
    _segment_voice_identity,
    _ssmd_pitch_semitones,
    _ssmd_rate_factor,
    _ssmd_volume_db,
)


def _plan(*, enabled: bool = True, same_voice_only: bool = True):
    source = f"""---
ssmd_version: "0.9"
voice_defaults:
  narrator:
    rate: brisk
    pitch: "+12%"
    volume: soft
  guest:
    rate: very-fast
    pitch: "-6%"
    volume: soft
prosody_transitions:
  enabled: {str(enabled).lower()}
  same_voice_only: {str(same_voice_only).lower()}
  rate: 100ms
  pitch: 200ms
  volume: 100ms
---
:::{{voice="narrator"}}
First utterance.
:::
:::{{voice="guest"}}
Second utterance.
:::
"""
    return compile_semantic_plan(
        document_from_text(source, input_format="ssmd"),
        planning=PlanningPolicy(document_format="ssmd", spacy_policy="off"),
    ).plan


def _entries(plan, tmp_path: Path, profile, *, scopes: tuple[str, str] = ("document", "document")):
    audio = np.zeros(1200, dtype=np.float32)
    path = tmp_path / "segment.wav"
    sf.write(path, audio, 24000, subtype="PCM_16")
    audio_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    entries = []
    for segment, scope_id in zip(plan.segments, scopes, strict=True):
        entries.append(
            (
                segment,
                {
                    "scope_id": scope_id,
                    "cache_path": path,
                    "audio_sha256": audio_hash,
                    "sample_rate": 24000,
                    "channels": 1,
                    "frames": len(audio),
                    "speech_hash": f"speech:{segment.id}",
                    "synthesis_key": f"synthesis:{segment.id}",
                    "prosody_transitions": plan.document_metadata["prosody_transitions"],
                    "voice_identity": _segment_voice_identity(profile, plan, scope_id, segment),
                },
            )
        )
    return entries


def _compose(entries):
    return _build_layout(
        None,
        entries,
        target_lufs=None,
        true_peak_ceiling_dbtp=-1.0,
        peak_policy="reduce_gain",
        clip_policy="clamp",
    )


def _speech_items(job):
    return [item for item in job.items if item.metadata.get("kind") == "speech"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("very-slow", 0.65),
        ("slow", 0.80),
        ("moderate", 0.90),
        ("normal", 1.00),
        ("brisk", 1.10),
        ("fast", 1.25),
        ("very-fast", 1.50),
        ("87%", 0.87),
        ("+10%", 1.10),
        ("-20%", 0.80),
    ],
)
def test_ssmd09_rate_values_are_converted_strictly(value, expected):
    assert _ssmd_rate_factor(value) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("value", "percentage"),
    [
        ("very-low", -20.0),
        ("low", -12.0),
        ("moderate-low", -6.0),
        ("normal", 0.0),
        ("moderate-high", 6.0),
        ("high", 12.0),
        ("very-high", 20.0),
        ("-3%", -3.0),
        ("+12%", 12.0),
    ],
)
def test_ssmd09_pitch_percentages_become_semitones(value, percentage):
    expected = 12.0 * math.log2(1.0 + percentage / 100.0)
    assert _ssmd_pitch_semitones(value) == pytest.approx(expected)


def test_ssmd09_volume_values_convert_to_decibels():
    assert _ssmd_volume_db("soft") == -6.0
    assert _ssmd_volume_db("5") == 12.0
    assert _ssmd_volume_db("-3dB") == -3.0


@pytest.mark.parametrize(
    ("converter", "value"),
    [
        (_ssmd_rate_factor, "x-fast"),
        (_ssmd_rate_factor, "0%"),
        (_ssmd_pitch_semitones, "medium"),
        (_ssmd_pitch_semitones, "-100%"),
        (_ssmd_volume_db, "very-loud"),
    ],
)
def test_unsupported_legacy_or_invalid_prosody_is_rejected(converter, value):
    with pytest.raises(InputError):
        converter(value)


def test_utterplan_v3_defaults_drive_audiojob_v2_envelopes(tmp_path):
    plan = _plan()
    assert len(plan.segments) == 2
    assert plan.segments[0].directives.prosody.rate == "brisk"
    assert plan.segments[0].directives.prosody.pitch == "+12%"
    assert plan.segments[1].directives.prosody.rate == "very-fast"
    assert plan.segments[1].directives.prosody.pitch == "-6%"

    profile = {
        "canonical": {
            "bindings_by_scope": {"document": {"narrator": "voice-a", "guest": "voice-a"}}
        }
    }
    job, identity = _compose(_entries(plan, tmp_path, profile))

    assert job.schema_version == 2
    first_ops = _speech_items(job)[0].operations
    assert any(isinstance(operation, Tempo) and operation.factor == 1.1 for operation in first_ops)
    assert any(
        isinstance(operation, PitchShift)
        and operation.semitones == pytest.approx(12.0 * math.log2(1.12))
        for operation in first_ops
    )

    second_ops = _speech_items(job)[1].operations
    envelopes = [op for op in second_ops if isinstance(op, RatePitchEnvelope)]
    assert len(envelopes) == 1
    assert not any(isinstance(op, (Tempo, PitchShift)) for op in second_ops)
    envelope = envelopes[0]
    assert envelope.rate[-1].seconds == pytest.approx(0.05)
    assert envelope.rate[-1].value == pytest.approx(1.5)
    assert envelope.pitch_semitones[-1].seconds == pytest.approx(0.05)
    assert envelope.pitch_semitones[-1].value == pytest.approx(12.0 * math.log2(0.94))
    assert sum(isinstance(op, Gain) for op in second_ops) == 1
    assert len(identity["warnings"]) == 1
    assert "volume transitions" in identity["warnings"][0]
    assert identity["identity_payload"]["prosody_transitions"]["document"]["rate"] == "100ms"

    saved_path = job.save(tmp_path / "audiojob-bundle")
    payload = json.loads(Path(saved_path).read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    restored = AudioJob.load(saved_path)
    assert restored.schema_version == 2
    assert isinstance(_speech_items(restored)[1].operations[0], RatePitchEnvelope)


def test_transition_voice_gate_and_scope_boundary_use_resolved_voice_identity(tmp_path):
    plan = _plan()
    same_voice_profile = {
        "canonical": {
            "bindings_by_scope": {
                "document": {"narrator": "voice-a", "guest": "voice-a"},
                "chapter-2": {"guest": "voice-a"},
            }
        }
    }
    job, _ = _compose(_entries(plan, tmp_path, same_voice_profile))
    assert any(isinstance(op, RatePitchEnvelope) for op in _speech_items(job)[1].operations)

    different_voice_profile = {
        "canonical": {
            "bindings_by_scope": {"document": {"narrator": "voice-a", "guest": "voice-b"}}
        }
    }
    job, _ = _compose(_entries(plan, tmp_path, different_voice_profile))
    assert not any(isinstance(op, RatePitchEnvelope) for op in _speech_items(job)[1].operations)
    assert any(isinstance(op, Tempo) for op in _speech_items(job)[1].operations)

    unrestricted_plan = _plan(same_voice_only=False)
    unrestricted_job, _ = _compose(_entries(unrestricted_plan, tmp_path, different_voice_profile))
    assert any(
        isinstance(op, RatePitchEnvelope) for op in _speech_items(unrestricted_job)[1].operations
    )

    disabled_plan = _plan(enabled=False)
    disabled_job, disabled_identity = _compose(
        _entries(disabled_plan, tmp_path, same_voice_profile)
    )
    assert not any(
        isinstance(op, RatePitchEnvelope) for op in _speech_items(disabled_job)[1].operations
    )
    assert disabled_identity["warnings"] == []

    scoped_entries = _entries(
        plan,
        tmp_path,
        same_voice_profile,
        scopes=("chapter-1", "chapter-2"),
    )
    scoped_job, _ = _compose(scoped_entries)
    assert not any(
        isinstance(op, RatePitchEnvelope) for op in _speech_items(scoped_job)[1].operations
    )


def test_zero_duration_transition_is_deterministic_and_affects_identity(tmp_path):
    plan = _plan()
    profile = {
        "canonical": {
            "bindings_by_scope": {"document": {"narrator": "voice-a", "guest": "voice-a"}}
        }
    }
    entries = _entries(plan, tmp_path, profile)
    zero_duration = {**plan.document_metadata["prosody_transitions"], "rate": "0ms", "pitch": "0ms"}
    for _, entry in entries:
        entry["prosody_transitions"] = zero_duration

    job, identity = _compose(entries)
    envelope = next(
        op for op in _speech_items(job)[1].operations if isinstance(op, RatePitchEnvelope)
    )
    assert envelope.rate[0].seconds == 0.0
    assert envelope.rate[0].value == pytest.approx(1.5)
    assert envelope.pitch_semitones[0].seconds == 0.0
    assert identity["identity_payload"]["prosody_transitions"]["document"]["pitch"] == "0ms"

    extended = {**zero_duration, "rate": "50ms"}
    for _, entry in entries:
        entry["prosody_transitions"] = extended
    _, extended_identity = _compose(entries)
    assert identity["composition_id"] != extended_identity["composition_id"]


def test_plan_aware_job_preserves_timing_anchor_and_resamples_output_rate():
    plan = _plan()
    segment = plan.segments[0]
    segment = replace(
        segment,
        directives=replace(segment.directives, prosody=None),
    )
    marker = Marker(
        id="marker-1",
        name="chapter-one",
        spoken_position=segment.spoken_start + 5,
    )
    plan = replace(plan, segments=(segment, *plan.segments[1:]), markers=(marker,))
    audio = np.sin(np.linspace(0.0, 8.0 * math.pi, 22050, dtype=np.float32))
    entry = {
        "scope_id": "document",
        "cache_path": None,
        "audio": audio,
        "audio_sha256": hashlib.sha256(audio.tobytes()).hexdigest(),
        "sample_rate": 22050,
        "channels": 1,
        "frames": len(audio),
        "speech_hash": "speech-1",
        "synthesis_key": "synthesis-1",
        "composition": {"rate": 1.1},
        "voice_identity": "voice-1",
        "word_timings": (
            SpeechWordTiming(
                text=segment.text,
                char_start=0,
                char_end=10,
                start_sample=0,
                end_sample=len(audio),
            ),
        ),
        "markers": _markers_by_segment(plan)[segment.id],
    }

    job, _identity = _build_layout(
        None,
        [(segment, entry)],
        target_lufs=None,
        true_peak_ceiling_dbtp=-1.0,
        peak_policy="reduce_gain",
        clip_policy="clamp",
        output_sample_rate=16000,
    )
    composed = Composer().compose(job)

    assert job.output.sample_rate == 16000
    assert job.items[0].anchors[0].sample_offset == round(len(audio) / 2)
    assert composed.sample_rate == 16000
    assert composed.markers[0].id == "marker-1"
    assert composed.markers[0].sample_offset == round(round((len(audio) / 2) / 1.1) * 16000 / 22050)
