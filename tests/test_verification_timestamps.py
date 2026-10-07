from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

import numpy as np
import pytest
import soundfile as sf

from readio.api import Readio, SynthesisRequest, TimestampSelfTestRequest
from readio.engines.base import RenderedSpeech, RequestMeasure, SpeechRequest, SpeechWordTiming
from readio.rendering.capacity import AtomicRequest, _merge_results, _RenderedChild
from readio.verification.alignment import tokenize_source
from readio.verification.timestamps import TimestampValidationError, compare_timestamps
from readio.verification.types import TranscriptionResult, TranscriptWord


def test_timestamp_alignment_preserves_spans_and_compares_merged_native_timings() -> None:
    text = "Time, time!"
    transcription = TranscriptionResult(
        text="time time",
        words=(
            TranscriptWord("time", 0.02, 0.20),
            TranscriptWord("TIME", 0.31, 0.52),
        ),
    )
    report = compare_timestamps(
        text=text,
        transcription=transcription,
        sample_rate=1_000,
        frames=1_000,
        native_timings=(
            {"text": "Time", "char_start": 0, "char_end": 4, "start_sample": 20, "end_sample": 200},
            {
                "text": "time",
                "char_start": 6,
                "char_end": 10,
                "start_sample": 300,
                "end_sample": 500,
            },
        ),
    )

    assert report["alignment"] == {
        "expected_words": 2,
        "recognized_words": 2,
        "matched_words": 2,
        "coverage": 1.0,
        "substitutions": 0,
        "deletions": 0,
        "insertions": 0,
    }
    assert [
        (row["text"], row["char_start"], row["char_end"]) for row in report["derived_word_timings"]
    ] == [("Time", 0, 4), ("time", 6, 10)]
    assert report["native_comparison"]["paired_words"] == 2
    assert report["native_comparison"]["start_error_ms"] == {
        "median": 5.0,
        "p95": 10.0,
        "max": 10.0,
    }
    assert report["native_comparison"]["end_error_ms"] == {
        "median": 10.0,
        "p95": 20.0,
        "max": 20.0,
    }


def test_comparison_uses_capacity_rebased_parent_timings() -> None:
    parent = SpeechRequest(id="parent", text="alpha beta", language="en-us")
    first_request = SpeechRequest(id="first", text="alpha", language="en-us")
    second_request = SpeechRequest(id="second", text="beta", language="en-us")
    first_measure = RequestMeasure(True, 1, 10, "model_tokens", "test")
    second_measure = RequestMeasure(True, 1, 10, "model_tokens", "test")
    first_result = RenderedSpeech(
        id="first",
        audio=np.zeros(100, dtype=np.float32),
        sample_rate=1_000,
        word_timings=(SpeechWordTiming("alpha", 0, 5, 20, 40),),
    )
    second_result = RenderedSpeech(
        id="second",
        audio=np.zeros(200, dtype=np.float32),
        sample_rate=1_000,
        word_timings=(SpeechWordTiming("beta", 0, 4, 30, 80),),
    )
    merged = _merge_results(
        parent,
        [
            _RenderedChild(first_request, 0, 5, first_measure, first_result),
            _RenderedChild(second_request, 6, 10, second_measure, second_result),
        ],
        (
            AtomicRequest(first_request, "parent", 0, 0, 5, first_measure),
            AtomicRequest(second_request, "parent", 1, 6, 10, second_measure),
        ),
    )

    assert merged.word_timings == (
        SpeechWordTiming("alpha", 0, 5, 20, 40),
        SpeechWordTiming("beta", 6, 10, 130, 180),
    )
    report = compare_timestamps(
        text=parent.text,
        transcription=TranscriptionResult(
            text="alpha beta",
            words=(TranscriptWord("alpha", 0.02, 0.04), TranscriptWord("beta", 0.13, 0.18)),
        ),
        sample_rate=1_000,
        frames=merged.audio.size,
        native_timings=tuple(asdict(timing) for timing in merged.word_timings),
    )
    assert report["native_comparison"]["paired_words"] == 2
    assert report["native_comparison"]["start_error_ms"] == {"median": 0.0, "p95": 0.0, "max": 0.0}


def test_derived_timestamp_cache_is_provenance_keyed_and_never_mutates_synthesis_sidecars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import readio.integrations.moondream as integration
    from readio.api import ProjectRef, TimestampGenerationRequest, VerificationOptions

    text = "source phrase"
    rate = 1_000
    audio = tmp_path / "canonical-segment.wav"
    sf.write(audio, np.zeros(rate, dtype=np.float32), rate)
    sidecar = tmp_path / ".readio" / "synthesis" / "cache" / "source.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text('{"word_timings": []}\n', encoding="utf-8")
    sidecar_before = sidecar.read_bytes()
    artifact = SimpleNamespace(
        scope_id="document",
        segment_id="segment-1",
        text=text,
        audio_path=audio,
        sample_rate=rate,
        frames=rate,
        audio_sha256="audio-sha",
        speech_hash="speech-hash",
        synthesis_key="synthesis-key",
        profile_id="profile",
        lowering_sha256="lowering-sha",
        word_timings=(),
    )
    session_calls: list[str] = []
    transcript = TranscriptionResult(
        text="source wrong",
        words=(TranscriptWord("source", 0.1, 0.2), TranscriptWord("wrong", 0.3, 0.4)),
    )

    class _Redux:
        load_seconds = 0.1

        def __init__(self, *, model: str, device: str) -> None:
            session_calls.append(f"{model}:{device}")

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def transcribe(self, _path: Path, *, timestamps: str) -> TranscriptionResult:
            assert timestamps == "word"
            return transcript

    class _Projects:
        def resolve_synthesis(self, _project: Any, request: Any) -> SimpleNamespace:
            return SimpleNamespace(
                language=request.language,
                engine=request.engine,
                model=request.model,
                voice=request.voice,
            )

        def plan(self, _project: Any, *, options: Any) -> SimpleNamespace:
            return SimpleNamespace(
                scopes=(SimpleNamespace(units=1),), attempt_id="attempt", repairs=0
            )

        def synthesize(self, _project: Any, _request: Any, *, activate: bool) -> SimpleNamespace:
            assert activate
            return SimpleNamespace(
                project=project,
                profile_id="profile",
                rendered=0,
                reused=1,
                selected_units=1,
                artifacts=(artifact,),
            )

    project = ProjectRef(
        root=tmp_path,
        project_id="project",
        name="project",
        kind="text",
        source_format="plain",
    )
    monkeypatch.setattr(integration, "ReduxSession", _Redux)
    version = ["redux-v1"]
    monkeypatch.setattr(integration, "moondream_version", lambda: version[0])
    app = Readio()
    app._services["ProjectService"] = _Projects()

    request = TimestampGenerationRequest(
        project=project,
        synthesis=SynthesisRequest(
            language="en-us", engine="kokoro", model="test", voice="af_sarah"
        ),
        verification=VerificationOptions(model="redux-a", device="cpu"),
    )
    first = app.verification.generate_timestamps(request)
    assert first.summary["alignment_coverage"] == 0.5
    assert first.results[0]["cache_status"] == "created"
    first_cache = Path(first.results[0]["cache_path"])
    first_record = json.loads(first_cache.read_text(encoding="utf-8"))
    assert first_record["format"] == "readio.verification.timestamps"
    assert first_record["identity"]["lowering_sha256"] == "lowering-sha"
    assert first_record["identity"]["audio_sha256"] == "audio-sha"
    assert len(first_record["word_timings"]) == 1

    cached = app.verification.generate_timestamps(request)
    assert cached.summary["cached"] == 1
    assert cached.results[0]["cache_status"] == "hit"
    assert len(session_calls) == 1

    changed_model = app.verification.generate_timestamps(
        TimestampGenerationRequest(
            project=project,
            synthesis=request.synthesis,
            verification=VerificationOptions(model="redux-b", device="cpu"),
        )
    )
    assert changed_model.results[0]["cache_key"] != first.results[0]["cache_key"]
    changed_model_record = json.loads(
        Path(changed_model.results[0]["cache_path"]).read_text(encoding="utf-8")
    )
    for field in ("speech_hash", "synthesis_key", "profile_id", "lowering_sha256", "audio_sha256"):
        assert changed_model_record["identity"][field] == first_record["identity"][field]
    assert changed_model.synthesis["reused_units"] == 1
    version[0] = "redux-v2"
    changed_version = app.verification.generate_timestamps(
        TimestampGenerationRequest(
            project=project,
            synthesis=request.synthesis,
            verification=VerificationOptions(model="redux-a", device="cpu"),
        )
    )
    assert changed_version.results[0]["cache_key"] != first.results[0]["cache_key"]
    assert len(session_calls) == 3

    changed_cache = Path(changed_version.results[0]["cache_path"])
    changed_cache.write_text('{"corrupt": true}\n', encoding="utf-8")
    repaired = app.verification.generate_timestamps(request)
    assert repaired.results[0]["cache_status"] == "created"
    assert len(session_calls) == 4
    assert sidecar.read_bytes() == sidecar_before
    assert changed_cache.parent == first.cache_root

    previous_key = repaired.results[0]["cache_key"]
    provenance_changes = (
        ("speech_hash", "speech-hash-2"),
        ("synthesis_key", "synthesis-key-2"),
        ("profile_id", "profile-2"),
        ("lowering_sha256", "lowering-sha-2"),
        ("audio_sha256", "audio-sha-2"),
        ("text", "source phrase revised"),
        ("sample_rate", 2_000),
        ("frames", 2_000),
    )
    for field, value in provenance_changes:
        setattr(artifact, field, value)
        invalidated = app.verification.generate_timestamps(request)
        assert invalidated.results[0]["cache_key"] != previous_key
        assert invalidated.results[0]["cache_status"] == "created"
        previous_key = invalidated.results[0]["cache_key"]
    assert sidecar.read_bytes() == sidecar_before


def test_only_exact_alignment_matches_receive_derived_timings() -> None:
    report = compare_timestamps(
        text="good word",
        transcription=TranscriptionResult(
            text="good wrong",
            words=(TranscriptWord("good", 0.1, 0.2), TranscriptWord("wrong", 0.3, 0.4)),
        ),
        sample_rate=1_000,
        frames=1_000,
    )

    assert report["alignment"]["substitutions"] == 1
    assert [timing["text"] for timing in report["derived_word_timings"]] == ["good"]
    assert report["timing_structure"]["status"] == "pass"


@pytest.mark.parametrize(
    "transcription",
    [
        TranscriptionResult(text="other", words=(TranscriptWord("other", 0.9, 1.1),)),
        TranscriptionResult(text="other", words=(TranscriptWord("other", 0.4, 0.3),)),
        TranscriptionResult(text="other", words=(TranscriptWord("other", float("nan"), 0.5),)),
    ],
)
def test_invalid_asr_timing_is_rejected_even_when_word_does_not_match(
    transcription: TranscriptionResult,
) -> None:
    with pytest.raises(TimestampValidationError):
        compare_timestamps(
            text="source",
            transcription=transcription,
            sample_rate=1_000,
            frames=1_000,
        )


def test_invalid_native_timing_uses_shared_timing_validator() -> None:
    with pytest.raises(TimestampValidationError, match="outside the rendered audio"):
        compare_timestamps(
            text="source",
            transcription=TranscriptionResult(
                text="source", words=(TranscriptWord("source", 0.1, 0.2),)
            ),
            sample_rate=1_000,
            frames=1_000,
            native_timings=(
                {
                    "text": "source",
                    "char_start": 0,
                    "char_end": 6,
                    "start_sample": 900,
                    "end_sample": 1_001,
                },
            ),
        )


def test_timestamp_selftest_reads_parent_segment_artifact_and_records_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import readio.integrations.moondream as integration

    text = "A parent segment verifies merged word coordinates."
    source_words = tokenize_source(text)
    rate = 1_000
    words = tuple(
        TranscriptWord(word.raw, index * 0.05, index * 0.05 + 0.04)
        for index, word in enumerate(source_words)
    )
    native_timings = tuple(
        {
            "text": word.raw,
            "char_start": word.char_start,
            "char_end": word.char_end,
            "start_sample": round(words[index].start_seconds * rate),
            "end_sample": round(words[index].end_seconds * rate),
        }
        for index, word in enumerate(source_words)
    )
    audio = tmp_path / "parent.wav"
    sf.write(audio, np.zeros(rate, dtype=np.float32), rate)
    artifact = SimpleNamespace(
        scope_id="document",
        segment_id="parent-segment",
        text=text,
        audio_path=audio,
        sample_rate=rate,
        frames=rate,
        audio_sha256="audio-sha",
        speech_hash="speech-hash",
        synthesis_key="synthesis-key",
        profile_id="profile",
        lowering_sha256="lowering-sha",
        word_timings=native_timings,
    )

    class _Redux:
        load_seconds = 0.1

        def __init__(self, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def transcribe(self, _path: Path, *, timestamps: str) -> TranscriptionResult:
            assert timestamps == "word"
            return TranscriptionResult(text=text, words=words)

    class _Projects:
        def create(self, _source: Path, *, output: Path) -> SimpleNamespace:
            return SimpleNamespace(root=output)

        def resolve_synthesis(
            self, _project: Any, request: Any, *, use_saved_settings: bool
        ) -> SimpleNamespace:
            assert use_saved_settings is False
            return SimpleNamespace(
                language=request.language,
                engine=request.engine,
                model=request.model,
                voice=request.voice,
            )

        def plan(self, _project: Any, *, options: Any) -> SimpleNamespace:
            return SimpleNamespace(
                scopes=(SimpleNamespace(units=len(source_words)),),
                renderability_mode=options.renderability,
                renderability_guaranteed=True,
                attempt_id="attempt",
                repairs=0,
                reused_scopes=(),
                rebuilt_scopes=("document",),
                diagnostics=(),
            )

        def synthesize(self, _project: Any, _request: Any, *, activate: bool) -> SimpleNamespace:
            assert activate
            return SimpleNamespace(
                profile_id="profile",
                rendered=1,
                reused=0,
                selected_units=1,
                artifacts=(artifact,),
            )

    monkeypatch.setattr(integration, "ReduxSession", _Redux)
    monkeypatch.setattr(integration, "moondream_version", lambda: "test-version")
    app = Readio()
    app._services["ProjectService"] = _Projects()

    result = app.verification.selftest_timestamps(
        TimestampSelfTestRequest(
            synthesis=SynthesisRequest(
                language="en-us", engine="kokoro", model="test", voice="af_sarah"
            ),
            output=tmp_path / "timestamp-selftest",
        )
    )

    assert result.overall_status == "pass"
    assert result.summary["matched_words"] == len(source_words)
    assert result.results[0]["native_comparison"]["paired_words"] == len(source_words)
    saved = json.loads(
        (tmp_path / "timestamp-selftest" / "result.json").read_text(encoding="utf-8")
    )
    assert saved["schema"] == "readio.verification.timestamp-selftest.v1"


def test_composition_does_not_load_or_call_derived_timestamp_generation() -> None:
    composition_source = (
        Path(__file__).parents[1] / "readio" / "stages" / "composition.py"
    ).read_text(encoding="utf-8")
    assert "ReduxSession" not in composition_source
    assert "moondream" not in composition_source
    assert "generate_timestamps" not in composition_source
