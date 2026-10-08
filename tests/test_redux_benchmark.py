from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

import readio.api as readio_api
from benchmarks.redux import benchmark_e2e, benchmark_voices
from benchmarks.redux.benchmark_e2e import SHORT_TAIL_CASE, summarize_short_tail
from benchmarks.redux.benchmark_voices import (
    aggregate_results,
    numeric_summary,
    safe_name,
    voice_wav_name,
)
from benchmarks.redux.common import (
    DEFAULT_REDUX_MODEL,
    DEFAULT_TEXT,
    BenchmarkCase,
    BenchmarkError,
    BenchmarkResult,
    character_error_rate,
    classify_verification,
    default_case,
    edit_distance,
    environment_metadata,
    inspect_wav,
    normalize_text,
    parse_engine_options,
    sha256_file,
    to_jsonable,
    verify,
    word_error_rate,
    write_json,
)


def test_default_case_uses_fixed_source_as_reference() -> None:
    case = default_case()
    assert case.text == DEFAULT_TEXT
    assert case.reference_text == DEFAULT_TEXT
    assert BenchmarkCase("spoken", "Dr. Smith", "Doctor Smith").reference_text == "Doctor Smith"


def test_engine_option_parser_coerces_scalar_values_conservatively() -> None:
    assert parse_engine_options(
        ["enabled=true", "disabled=false", "missing=null", "count=4", "ratio=0.3", "label=warm"]
    ) == {
        "enabled": True,
        "disabled": False,
        "missing": None,
        "count": 4,
        "ratio": 0.3,
        "label": "warm",
    }
    with pytest.raises(ValueError, match="KEY=VALUE"):
        parse_engine_options(["missing-separator"])


def test_benchmark_clis_accept_repeatable_engine_options() -> None:
    for module in (benchmark_e2e, benchmark_voices):
        args = module.build_parser().parse_args(
            ["--engine-option", "temperature=0.3", "--engine-option", "frames_after_eos=0"]
        )
        assert parse_engine_options(args.engine_option) == {
            "temperature": 0.3,
            "frames_after_eos": 0,
        }


def test_short_tail_summary_requires_exact_terminal_transcript_and_reports_metrics() -> None:
    results = (
        BenchmarkResult(
            status="pass",
            case=SHORT_TAIL_CASE.name,
            total_seconds=1.0,
            verification=verify(
                expected=SHORT_TAIL_CASE.reference_text,
                transcript=SHORT_TAIL_CASE.reference_text,
            ),
        ),
        BenchmarkResult(
            status="fail",
            case=SHORT_TAIL_CASE.name,
            total_seconds=2.0,
            verification=verify(
                expected=SHORT_TAIL_CASE.reference_text, transcript="Hello, how are"
            ),
        ),
    )

    report = summarize_short_tail(results)

    assert SHORT_TAIL_CASE.text == "Hello, how are you?"
    assert (report["runs"], report["pass_count"], report["fail_count"]) == (2, 1, 1)
    assert report["attempts"][0]["terminal_complete"] is True
    assert report["attempts"][1]["terminal_complete"] is False
    assert report["wer"] == {"median": pytest.approx(1 / 8), "worst": pytest.approx(1 / 4)}
    assert report["duration_seconds"] == {"min": 1.0, "median": 1.5, "max": 2.0}


def test_repeated_short_tail_runner_executes_packaged_api_case_each_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, requests = _fake_verification_app()
    monkeypatch.setattr(readio_api, "Readio", lambda: app)

    report = benchmark_e2e.run_repeated_short_tail(tmp_path, repetitions=3)

    assert report["runs"] == 3
    assert report["pass_count"] == 3
    assert len(requests) == 1
    request = requests[0]
    assert request.case == "pocket-short-tail-v1"
    assert request.repetitions == 3
    assert request.output == tmp_path
    assert request.synthesis.language == "en"


def test_normalize_text_handles_nfkc_case_punctuation_apostrophes_and_spacing() -> None:
    assert normalize_text("  ＣＬＥＡＲ—It’s  GOOD!\n") == "clear it's good"
    assert normalize_text("Rock ‘n’ roll…") == "rock 'n' roll"


def test_edit_distance_counts_substitution_deletion_and_insertion() -> None:
    substitution = edit_distance("cat", "cut")
    assert (substitution.distance, substitution.substitutions) == (1, 1)
    assert (substitution.deletions, substitution.insertions) == (0, 0)

    deletion = edit_distance(["one", "two", "three"], ["one", "three"])
    assert (deletion.distance, deletion.deletions, deletion.insertions) == (1, 1, 0)

    insertion = edit_distance(["one", "three"], ["one", "two", "three"])
    assert (insertion.distance, insertion.deletions, insertion.insertions) == (1, 0, 1)


def test_word_error_rate_examples_and_normalization() -> None:
    assert word_error_rate("one two three", "one two three") == 0.0
    assert word_error_rate("one two three", "one three") == pytest.approx(1 / 3)
    assert word_error_rate("one two three", "one four three") == pytest.approx(1 / 3)
    assert word_error_rate("One, two!", "one two") == 0.0


def test_character_error_rate_ignores_spaces_and_punctuation() -> None:
    assert character_error_rate("one two", "onetwo") == 0.0
    assert character_error_rate("cat", "cut") == pytest.approx(1 / 3)


def test_empty_reference_error_rates_are_finite() -> None:
    assert word_error_rate("", "") == 0.0
    assert word_error_rate("", "extra words") == 1.0
    assert character_error_rate("", "x") == 1.0


def test_classification_threshold_boundaries_and_empty_transcript() -> None:
    assert classify_verification(wer=0.10, cer=0.05, transcript="heard") == "pass"
    assert classify_verification(wer=0.11, cer=0.06, transcript="heard") == "review"
    assert classify_verification(wer=0.21, cer=0.01, transcript="heard") == "fail"
    assert classify_verification(wer=0.0, cer=0.0, transcript="  !!! ") == "fail"


def test_verify_preserves_original_and_normalized_strings_and_counts() -> None:
    result = verify(expected="One two three.", transcript="one three")
    assert result.expected == "One two three."
    assert result.transcript == "one three"
    assert result.normalized_expected == "one two three"
    assert result.normalized_transcript == "one three"
    assert result.wer == pytest.approx(1 / 3)
    assert result.wer_deletions == 1
    assert result.status == "fail"


def test_inspect_wav_checks_contract_and_hashes_file(tmp_path: Path) -> None:
    path = tmp_path / "master.wav"
    sf.write(path, np.array([0.1, -0.1, 0.2, -0.2], dtype=np.float32), 16_000)

    audio = inspect_wav(path)

    assert (audio.frames, audio.sample_rate, audio.channels) == (4, 16_000, 1)
    assert audio.duration_seconds == pytest.approx(4 / 16_000)
    assert len(sha256_file(path)) == 64


def test_inspect_wav_rejects_wrong_rate_channels_and_missing_files(tmp_path: Path) -> None:
    wrong_rate = tmp_path / "wrong-rate.wav"
    sf.write(wrong_rate, np.array([0.1, 0.2], dtype=np.float32), 22_050)
    with pytest.raises(BenchmarkError, match="sample rate"):
        inspect_wav(wrong_rate)

    stereo = tmp_path / "stereo.wav"
    sf.write(stereo, np.array([[0.1, 0.1], [0.2, 0.2]], dtype=np.float32), 16_000)
    with pytest.raises(BenchmarkError, match="channels"):
        inspect_wav(stereo)

    with pytest.raises(BenchmarkError, match="does not exist"):
        inspect_wav(tmp_path / "missing.wav")


def test_json_serialization_is_stable_and_handles_paths_and_nonfinite_values(
    tmp_path: Path,
) -> None:
    result = BenchmarkResult(
        case="example",
        voice="voice",
        wav=str(tmp_path / "master.wav"),
        plan_ids=("plan-a", "plan-b"),
        verification=verify(expected="hello", transcript="hello"),
        environment={"optional": None, "unavailable_metric": float("nan")},
    )
    converted = to_jsonable(result)
    assert converted["plan_ids"] == ["plan-a", "plan-b"]
    assert converted["environment"]["unavailable_metric"] is None

    output = tmp_path / "nested" / "result.json"
    write_json(output, result)
    text = output.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert json.loads(text)["schema"] == "readio.benchmark.redux.v1"
    assert text == json.dumps(json.loads(text), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def test_environment_metadata_records_nullable_package_versions() -> None:
    metadata = environment_metadata(engine="unknown-engine")
    assert metadata["timestamp_utc"].endswith("+00:00")
    assert metadata["engine_package"] == "unknown-engine"
    assert environment_metadata(engine="inflect")["engine_package"] == "inflectsynth"
    assert metadata["redux_model"] == "moondream/parakeet-redux"
    assert "moondream_version" in metadata
    assert "cpu_count" in metadata


def _fake_verification_app(*, failure_stage: str | None = None):
    requests = []

    class Verification:
        def run_e2e(self, request):
            requests.append(request)
            expected = (
                SHORT_TAIL_CASE.reference_text
                if request.case == "pocket-short-tail-v1"
                else DEFAULT_TEXT
            )
            status = "fail" if failure_stage else "pass"
            attempt = {
                "ordinal": 1,
                "overall_status": status,
                "failure_stage": failure_stage,
                "project_path": str(Path(request.output or ".") / "attempt-001" / "project.readio"),
                "source_sha256": "a" * 64,
                "resolved": {
                    "language": request.synthesis.language,
                    "engine": request.synthesis.engine,
                    "model": request.synthesis.model,
                    "voice": request.synthesis.voice,
                    "voice_ref": request.synthesis.voice,
                },
                "planning": {
                    "status": "pass",
                    "attempt_id": "plan-1",
                    "scope_count": 1,
                    "planned_units": 1,
                },
                "synthesis": {
                    "profile_id": "profile-1",
                    "selected_units": 1,
                    "rendered_units": 1,
                    "reused_units": 0,
                    "activated": True,
                },
                "composition": {
                    "composition_id": "composition-1",
                    "items": 1,
                    "audio_seconds": 1.0,
                    "sample_rate": 16_000,
                    "channels": 1,
                    "wav": str(Path(request.output or ".") / "attempt-001" / "master.wav"),
                    "wav_sha256": "b" * 64,
                },
                "verification": {"status": status, "wer": 0.0, "cer": 0.0, "transcript": expected},
                "timings": {
                    "project_seconds": 0.1,
                    "resolution_seconds": 0.1,
                    "planning_seconds": 0.1,
                    "synthesis_seconds": 0.1,
                    "composition_seconds": 0.1,
                    "redux_seconds": 0.1,
                },
                "seconds": 0.6,
                "error": {"message": "resolved engine piper is not supported"}
                if failure_stage
                else None,
            }
            return SimpleNamespace(
                to_dict=lambda: {
                    "overall_status": status,
                    "verification": {"backend_package_version": "1.2.3"},
                    "timings": {"total_seconds": 0.6, "backend_load_seconds": 0.2},
                    "output": request.output,
                    "error": attempt["error"],
                    "failure_stage": failure_stage,
                },
                attempts=tuple(
                    dict(attempt, ordinal=index) for index in range(1, request.repetitions + 1)
                ),
                error=attempt["error"],
                failure_stage=failure_stage,
                timings={"total_seconds": 0.6},
            )

    return SimpleNamespace(verification=Verification()), requests


def test_e2e_cli_writes_json_result_without_loading_real_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    app, requests = _fake_verification_app()
    monkeypatch.setattr(readio_api, "Readio", lambda: app)
    status = benchmark_e2e.main(["--work-dir", str(tmp_path), "--json"])

    assert status == 0
    assert len(requests) == 1
    assert requests[0].case == "readio-e2e-en-v1"
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "pass"
    assert result["verification"]["transcript"] == DEFAULT_TEXT
    assert json.loads(capsys.readouterr().out)["schema"] == "readio.benchmark.redux.v1"


def test_e2e_cli_persists_preflight_failure_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    app, requests = _fake_verification_app(failure_stage="preflight")
    monkeypatch.setattr(readio_api, "Readio", lambda: app)
    status = benchmark_e2e.main(["--work-dir", str(tmp_path), "--json"])

    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert status == 2
    assert len(requests) == 1
    assert result["status"] == "fail"
    assert result["failure_stage"] == "preflight"
    assert "resolved engine" in result["error"]
    assert json.loads(capsys.readouterr().out)["status"] == "fail"


def test_safe_voice_names_include_ordinal_and_sanitize_components() -> None:
    assert safe_name(" AF_Sarah / Voice! ") == "af-sarah-voice"
    assert voice_wav_name(1, "kokoro", "v1.0", "af_sarah") == ("001-kokoro-v1-0-af-sarah.wav")
    assert voice_wav_name(2, "kokoro", "v1.0", "af_sarah") != voice_wav_name(
        1, "kokoro", "v1.0", "af_sarah"
    )
    with pytest.raises(ValueError, match="positive"):
        voice_wav_name(0, "kokoro", "v1.0", "af_sarah")


def test_numeric_summary_and_voice_aggregation_include_outliers() -> None:
    stats = numeric_summary(list(range(20)))
    assert stats == {"mean": 9.5, "median": 9.5, "min": 0.0, "max": 19.0, "p95": 18.0}
    assert numeric_summary([None, float("nan")]) is None

    results = (
        BenchmarkResult(
            status="pass",
            voice="af_bella",
            verification=verify(expected="one two", transcript="one two"),
            synthesis_seconds=1.0,
            synthesis_x_real_time=5.0,
        ),
        BenchmarkResult(
            status="review",
            voice="af_heart",
            verification=verify(expected="one two", transcript="one three"),
            synthesis_seconds=2.0,
            synthesis_x_real_time=3.0,
        ),
        BenchmarkResult(status="fail", voice="af_fail", error="bad voice"),
    )
    summary = aggregate_results(results)
    assert summary["voice_count"] == 3
    assert (summary["passed"], summary["review"], summary["failed"]) == (1, 1, 1)
    assert summary["wer"]["mean"] == pytest.approx(0.25)
    assert summary["slowest_synthesis"] == {"voice": "af_heart", "synthesis_seconds": 2.0}
    assert summary["highest_wer"] == {"voice": "af_heart", "wer": 0.5}


def test_voice_matrix_compatibility_cli_delegates_to_api_and_writes_json_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "matrix"
    api_wav = tmp_path / "api-master.wav"
    api_wav.write_bytes(b"test wav placeholder")
    requests = []

    def row(voice_id: str, *, transcript: str, status: str, error: str | None = None):
        return {
            "voice": {
                "ref": f"kokoro:v1.0/{voice_id}",
                "id": voice_id,
                "engine": "kokoro",
                "model": "v1.0",
                "language": "en-us",
            },
            "status": status,
            "planning_status": "pass",
            "synthesis_status": "pass",
            "resolved": {
                "language": "en-us",
                "engine": "kokoro",
                "model": "v1.0",
                "voice": voice_id,
                "voice_ref": f"kokoro:v1.0/{voice_id}",
            },
            "planning": {
                "status": "pass",
                "attempt_id": "plan-1",
                "scope_count": 1,
                "planned_units": 1,
            },
            "synthesis": {
                "profile_id": "profile-1",
                "selected_units": 1,
                "rendered_units": 1,
                "reused_units": 0,
                "activated": True,
            },
            "composition": {
                "composition_id": "composition-1",
                "items": 1,
                "audio_seconds": 1.0,
                "sample_rate": 16_000,
                "channels": 1,
                "wav": str(api_wav),
                "wav_sha256": "b" * 64,
            },
            "verification": {"status": status, "transcript": transcript, "wer": 0.0, "cer": 0.0},
            "timings": {"synthesis_seconds": 0.2, "redux_seconds": 0.1},
            "source_sha256": "a" * 64,
            "project_path": str(tmp_path / voice_id / "project.readio"),
            "error": {"message": error} if error else None,
            "failure_stage": "verification" if error else None,
        }

    rows = (
        row("af_bella", transcript=DEFAULT_TEXT, status="pass"),
        row("af_heart", transcript="", status="fail", error="wrong-engine catalog entry"),
    )
    matrix_document = {
        "schema": "readio.verification.voice-matrix.v1",
        "overall_status": "fail",
        "query": {"language": "en-us", "engine": "kokoro", "model": "v1.0"},
        "redux": {"model": DEFAULT_REDUX_MODEL, "device": "cpu", "load_seconds": 0.5},
        "results": rows,
        "error": None,
    }

    class Verification:
        def generate_voices(self, request):
            requests.append(request)
            return SimpleNamespace(
                to_dict=lambda: matrix_document,
                query=matrix_document["query"],
                redux=matrix_document["redux"],
                results=rows,
                error=None,
            )

    monkeypatch.setattr(readio_api, "Readio", lambda: SimpleNamespace(verification=Verification()))
    status = benchmark_voices.main(["--work-dir", str(output), "--json"])

    document = json.loads((output / "results.json").read_text(encoding="utf-8"))
    csv_text = (output / "results.csv").read_text(encoding="utf-8")
    assert len(requests) == 1
    assert requests[0].output == output / "verification"
    assert requests[0].include_experimental is False
    assert [result["voice"] for result in document["results"]] == ["af_bella", "af_heart"]
    assert document["summary"]["passed"] == 1
    assert document["summary"]["failed"] == 1
    assert (output / "wav" / "001-kokoro-v1-0-af-bella.wav").is_file()
    assert "transcript" not in csv_text.casefold()
    assert DEFAULT_TEXT not in csv_text
    assert "wrong-engine" in document["results"][1]["error"]
    assert json.loads(capsys.readouterr().out)["schema"] == "readio.benchmark.redux.voice-matrix.v1"
    assert status == 1
