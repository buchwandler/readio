from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import numpy as np
import pytest
import soundfile as sf
from typing_extensions import Self

import readio.api as readio_api
from benchmarks.redux import benchmark_e2e, benchmark_voices
from benchmarks.redux.common import (
    DEFAULT_TEXT,
    SHORT_TAIL_CASE,
    BenchmarkCase,
    BenchmarkError,
    BenchmarkResult,
    ReduxTranscriber,
    Transcript,
    TranscriptWord,
    aggregate_results,
    character_error_rate,
    classify_verification,
    default_case,
    edit_distance,
    environment_metadata,
    filter_voices,
    inspect_wav,
    normalize_text,
    numeric_summary,
    parse_engine_options,
    run_case,
    safe_name,
    sha256_file,
    summarize_short_tail,
    to_jsonable,
    verify,
    voice_wav_name,
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


def test_repeated_short_tail_runner_executes_selected_phrase_each_time(tmp_path: Path, monkeypatch):
    calls = []
    monkeypatch.setattr(readio_api, "Readio", lambda: object())

    class ReusedRedux:
        def __enter__(self):
            return _FakeRedux()

        def __exit__(self, *_args):
            return None

    def fake_run_case(**kwargs):
        calls.append(kwargs)
        return BenchmarkResult(
            status="pass",
            case=kwargs["case"].name,
            total_seconds=1.0,
            verification=verify(
                expected=kwargs["case"].reference_text,
                transcript=kwargs["case"].reference_text,
            ),
        )

    monkeypatch.setattr(benchmark_e2e, "ReduxTranscriber", lambda: ReusedRedux())
    monkeypatch.setattr(benchmark_e2e, "run_case", fake_run_case)

    report = benchmark_e2e.run_repeated_short_tail(tmp_path, repetitions=3)

    assert report["runs"] == 3
    assert report["pass_count"] == 3
    assert [call["case"].text for call in calls] == ["Hello, how are you?"] * 3
    assert len({call["project_dir"] for call in calls}) == 3


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
    assert metadata["redux_model"] == "moondream/parakeet-redux"
    assert "moondream_version" in metadata
    assert "cpu_count" in metadata


def test_redux_adapter_loads_context_and_converts_word_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []

    class Speech:
        def transcribe(self, *, audio: Path, timestamps: str) -> dict[str, object]:
            calls.append((audio, timestamps))
            return {
                "text": "hello there",
                "timestamps": [
                    {"text": "hello", "start": 0.0, "end": 0.2},
                    {"word": "there", "start": 0.21, "end": 0.4},
                ],
            }

    class Photon:
        def __enter__(self) -> Speech:
            calls.append("entered")
            return Speech()

        def __exit__(self, *_: object) -> None:
            calls.append("exited")

    fake_moondream = ModuleType("moondream")

    def fake_photon(model: str, *, device: str) -> Photon:
        calls.append((model, device))
        return Photon()

    fake_moondream.__dict__["photon"] = fake_photon
    monkeypatch.setitem(sys.modules, "moondream", fake_moondream)

    wav = tmp_path / "input.wav"
    transcriber = ReduxTranscriber()
    with transcriber as redux:
        transcript = redux.transcribe(wav)
        assert redux.load_seconds is not None

    assert isinstance(transcript, Transcript)
    assert transcript.text == "hello there"
    assert transcript.words == (
        TranscriptWord("hello", 0.0, 0.2),
        TranscriptWord("there", 0.21, 0.4),
    )
    assert calls == [
        ("moondream/parakeet-redux", "cpu"),
        "entered",
        (wav, "word"),
        "exited",
    ]


def test_redux_adapter_requires_context_before_transcribing(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="must be entered"):
        ReduxTranscriber().transcribe(tmp_path / "audio.wav")


def _fake_readio(tmp_path: Path, *, resolved_engine: str = "kokoro") -> tuple[object, list[str]]:
    calls: list[str] = []
    project_path = tmp_path / "project.readio"

    class Projects:
        def create(self, source: Path, *, output: Path) -> SimpleNamespace:
            calls.append("create")
            assert source.read_text(encoding="utf-8") == DEFAULT_TEXT
            assert output == project_path
            return SimpleNamespace(project_id="project-1", root=output)

        def resolve_synthesis(
            self, _project: Any, request: Any, *, use_saved_settings: bool
        ) -> SimpleNamespace:
            calls.append("resolve_synthesis")
            assert use_saved_settings is False
            assert request.language == "en-us"
            assert request.engine == "kokoro"
            assert request.model == "v1.0"
            assert request.voice == "kokoro:v1.0/af_sarah"
            if hasattr(self, "expected_engine_options"):
                assert request.engine_options == self.expected_engine_options
            return SimpleNamespace(
                engine=resolved_engine,
                model="v1.0",
                language="en-us",
                voice="af_sarah",
            )

        def plan(self, _project: object) -> SimpleNamespace:
            calls.append("plan")
            return SimpleNamespace(scopes=(SimpleNamespace(plan_id="plan-1", units=2),))

        def synthesize(self, _project: Any, _request: Any, *, activate: bool) -> SimpleNamespace:
            calls.append("synthesize")
            assert activate is True
            return SimpleNamespace(
                profile_id="profile-1",
                selected_units=2,
                rendered=2,
                reused=0,
                activated=True,
            )

        def compose(self, _project: Any, options: Any) -> SimpleNamespace:
            calls.append("compose")
            assert options.sample_rate == 16_000
            wav = tmp_path / "master.wav"
            sf.write(wav, np.full(160, 0.1, dtype=np.float32), 16_000)
            return SimpleNamespace(
                composition_id="composition-1",
                master_path=wav,
                frames=160,
                items=1,
                loudness=None,
            )

    class Catalog:
        def resolve_voice(self, voice: str, *, engine: str) -> SimpleNamespace:
            calls.append("resolve_voice")
            assert voice == "kokoro:v1.0/af_sarah"
            assert engine == "kokoro"
            return SimpleNamespace(
                ref=voice,
                voice="af_sarah",
                catalog_entry=SimpleNamespace(ref=voice, id="af_sarah"),
            )

    return SimpleNamespace(projects=Projects(), catalog=Catalog()), calls


class _FakeRedux:
    model = "mock/redux"
    device = "cpu"
    load_seconds = 0.25

    def transcribe(self, wav: Path) -> Transcript:
        assert wav.is_file()
        return Transcript(DEFAULT_TEXT)


def test_run_case_executes_explicit_public_api_pipeline(tmp_path: Path) -> None:
    app, calls = _fake_readio(tmp_path)
    result = run_case(
        app=app,
        case=default_case(),
        language="en-us",
        engine="kokoro",
        model="v1.0",
        voice="kokoro:v1.0/af_sarah",
        project_dir=tmp_path / "project.readio",
        source_path=tmp_path / "source.txt",
        redux=_FakeRedux(),  # type: ignore[arg-type]
    )

    assert calls == [
        "create",
        "resolve_voice",
        "resolve_synthesis",
        "plan",
        "synthesize",
        "compose",
    ]
    assert result.status == "pass"
    assert result.project_id == "project-1"
    assert result.plan_ids == ("plan-1",)
    assert result.planned_units == 2
    assert result.sample_rate == 16_000
    assert result.channels == 1
    assert result.verification is not None
    assert result.verification.wer == 0.0
    assert result.redux_load_seconds == 0.25
    assert result.input_sha256 is not None
    assert result.wav_sha256 is not None


def test_run_case_merges_engine_device_and_redacts_credentials_from_result(tmp_path: Path) -> None:
    app, _calls = _fake_readio(tmp_path)
    expected_options = {
        "temperature": 0.3,
        "api_token": "secret-value",
        "device": "cuda",
    }
    app.projects.expected_engine_options = expected_options

    result = run_case(
        app=app,
        case=default_case(),
        language="en-us",
        engine="kokoro",
        model="v1.0",
        voice="kokoro:v1.0/af_sarah",
        project_dir=tmp_path / "project.readio",
        source_path=tmp_path / "source.txt",
        engine_device="cuda",
        engine_options={"temperature": 0.3, "api_token": "secret-value", "device": "ignored"},
        redux=_FakeRedux(),  # type: ignore[arg-type]
    )

    assert result.engine_options == {
        "temperature": 0.3,
        "api_token": "<redacted>",
        "device": "cuda",
    }
    assert "secret-value" not in json.dumps(result.to_dict())


def test_pocket_voice_benchmark_preflight_preserves_regional_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from readio.engines.catalog import CatalogResult, SynthesisTarget, TargetVoice
    from readio.voices import resolve_voice_reference

    target = SynthesisTarget(
        engine="pocket",
        id="english_2026-04",
        display_name="English Pocket",
        languages=("en",),
        voices=("alba",),
        voice_details=(
            TargetVoice(
                id="alba",
                gender="unknown",
                language="en",
                locale="en",
                language_label="English",
                languages=("en",),
            ),
        ),
    )
    import readio.voices as voices_module

    monkeypatch.setattr(
        voices_module,
        "discover_targets",
        lambda **_kwargs: CatalogResult(targets=(target,)),
    )
    requests: list[str] = []

    class Projects:
        def create(self, _source: Path, *, output: Path) -> SimpleNamespace:
            return SimpleNamespace(project_id="pocket-project", root=output)

        def resolve_synthesis(
            self, _project: object, request: Any, *, use_saved_settings: bool
        ) -> SimpleNamespace:
            assert use_saved_settings is False
            requests.append(request.language)
            resolution = resolve_voice_reference(
                request.voice,
                language=request.language,
                model=request.model,
                source=None,
                engine=request.engine,
            )
            assert resolution is not None
            return SimpleNamespace(
                engine=resolution.engine,
                model=resolution.target_id,
                language=resolution.language,
                voice=resolution.voice,
            )

        def plan(self, _project: object) -> SimpleNamespace:
            return SimpleNamespace(scopes=(SimpleNamespace(plan_id="plan", units=1),))

        def synthesize(
            self, _project: object, _request: object, *, activate: bool
        ) -> SimpleNamespace:
            assert activate is True
            return SimpleNamespace(
                profile_id="profile",
                selected_units=1,
                rendered=1,
                reused=0,
                activated=True,
            )

        def compose(self, _project: object, _options: Any) -> SimpleNamespace:
            wav = tmp_path / "pocket-master.wav"
            sf.write(wav, np.full(160, 0.1, dtype=np.float32), 16_000)
            return SimpleNamespace(
                composition_id="composition",
                master_path=wav,
                frames=160,
                items=1,
                loudness=None,
            )

    class Catalog:
        def resolve_voice(self, voice: str, *, engine: str) -> Any:
            return resolve_voice_reference(
                voice,
                language=None,
                model=None,
                source=None,
                engine=engine,
            )

    result = run_case(
        app=SimpleNamespace(projects=Projects(), catalog=Catalog()),
        case=default_case(),
        language="en-US",
        engine="pocket",
        model="english_2026-04",
        voice="pocket:english_2026-04/alba",
        project_dir=tmp_path / "pocket-project.readio",
        source_path=tmp_path / "pocket-source.txt",
        redux=_FakeRedux(),  # type: ignore[arg-type]
    )

    assert result.status == "pass"
    assert result.resolved_language == "en-us"
    assert requests == ["en-US"]


def test_run_case_records_preflight_failure_without_running_later_stages(tmp_path: Path) -> None:
    app, calls = _fake_readio(tmp_path, resolved_engine="piper")
    result = run_case(
        app=app,
        case=default_case(),
        language="en-us",
        engine="kokoro",
        model="v1.0",
        voice="kokoro:v1.0/af_sarah",
        project_dir=tmp_path / "project.readio",
        source_path=tmp_path / "source.txt",
        redux=_FakeRedux(),  # type: ignore[arg-type]
    )

    assert result.status == "fail"
    assert result.failure_stage == "preflight"
    assert "resolved engine" in (result.error or "")
    assert calls == ["create", "resolve_voice", "resolve_synthesis"]
    assert result.input_sha256 is not None
    assert result.to_dict()["status"] == "fail"


def test_e2e_cli_writes_json_result_without_loading_real_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    app, _ = _fake_readio(tmp_path)
    monkeypatch.setattr(readio_api, "Readio", lambda: app)

    class MockTranscriber(_FakeRedux):
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(
        benchmark_e2e,
        "ReduxTranscriber",
        lambda **_kwargs: MockTranscriber(),
    )

    status = benchmark_e2e.main(["--work-dir", str(tmp_path), "--json"])

    assert status == 0
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "pass"
    assert result["verification"]["transcript"] == DEFAULT_TEXT
    assert json.loads(capsys.readouterr().out)["schema"] == "readio.benchmark.redux.v1"


def test_e2e_cli_persists_preflight_failure_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    app, _ = _fake_readio(tmp_path, resolved_engine="piper")
    monkeypatch.setattr(readio_api, "Readio", lambda: app)

    class MockTranscriber(_FakeRedux):
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(
        benchmark_e2e,
        "ReduxTranscriber",
        lambda **_kwargs: MockTranscriber(),
    )

    status = benchmark_e2e.main(["--work-dir", str(tmp_path), "--json"])

    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert status == 2
    assert result["status"] == "fail"
    assert result["failure_stage"] == "preflight"
    assert "resolved engine" in result["error"]
    assert json.loads(capsys.readouterr().out)["status"] == "fail"


def test_filter_voices_preserves_catalog_order_and_experimental_opt_in() -> None:
    voices = (
        SimpleNamespace(id="first", runtime_available=True, experimental=False),
        SimpleNamespace(id="preview", runtime_available=True, experimental=True),
        SimpleNamespace(id="missing", runtime_available=False, experimental=False),
    )

    assert [voice.id for voice in filter_voices(voices)] == ["first"]
    assert [voice.id for voice in filter_voices(voices, include_experimental=True)] == [
        "first",
        "preview",
    ]


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


def _fake_voice_app() -> tuple[Any, list[Any]]:
    def voice_info(voice_id: str, *, experimental: bool = False, runtime: bool = True):
        return readio_api.VoiceInfo(
            ref=f"kokoro:v1.0/{voice_id}",
            id=voice_id,
            gender="female",
            language="en-us",
            locale="en-us",
            language_label="English",
            model="v1.0",
            source="test",
            default=False,
            status="ready",
            experimental=experimental,
            runtime_available=runtime,
            engine="kokoro",
        )

    voices = [
        voice_info("af_bella"),
        voice_info("af_heart"),
        voice_info("af_experimental", experimental=True),
        voice_info("af_missing", runtime=False),
    ]
    requests: list[Any] = []

    class Projects:
        def create(self, source: Path, *, output: Path) -> SimpleNamespace:
            assert source.read_text(encoding="utf-8") == DEFAULT_TEXT
            return SimpleNamespace(project_id=output.name, root=output)

        def resolve_synthesis(
            self, _project: Any, request: Any, *, use_saved_settings: bool
        ) -> SimpleNamespace:
            requests.append(request)
            assert use_saved_settings is False
            voice = request.voice.rsplit("/", 1)[-1]
            engine = "wrong-engine" if voice == "af_heart" else "kokoro"
            return SimpleNamespace(
                engine=engine,
                model="v1.0",
                language="en-us",
                voice=voice,
            )

        def plan(self, _project: object) -> SimpleNamespace:
            return SimpleNamespace(scopes=(SimpleNamespace(plan_id="plan", units=2),))

        def synthesize(self, _project: Any, _request: Any, *, activate: bool) -> SimpleNamespace:
            return SimpleNamespace(
                profile_id="profile",
                selected_units=2,
                rendered=2,
                reused=0,
                activated=activate,
            )

        def compose(self, project: Any, _options: Any) -> SimpleNamespace:
            root = project.root
            wav = root.parent / f"{root.name}.wav"
            sf.write(wav, np.full(320, 0.1, dtype=np.float32), 16_000)
            return SimpleNamespace(
                composition_id="composition",
                master_path=wav,
                frames=320,
                items=1,
                loudness=None,
            )

    class Catalog:
        def voices(self, query: Any) -> tuple[Any, ...]:
            assert query.language == "en-us"
            assert query.engine == "kokoro"
            assert query.model == "v1.0"
            return tuple(voices)

        def resolve_voice(self, reference: str, *, engine: str) -> SimpleNamespace:
            assert engine == "kokoro"
            voice = next(item for item in voices if item.ref == reference)
            return SimpleNamespace(
                ref=voice.ref,
                voice=voice.id,
                catalog_entry=voice,
            )

    return SimpleNamespace(projects=Projects(), catalog=Catalog()), requests


class _SharedMockRedux(_FakeRedux):
    instances = 0
    enters = 0

    def __init__(self, *, model: str, device: str) -> None:
        self.model = model
        self.device = device
        self.load_seconds = 0.5
        type(self).instances += 1

    def __enter__(self) -> Self:
        type(self).enters += 1
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def transcribe(self, wav: Path) -> Transcript:
        if "af-heart" in wav.name:
            return Transcript("")
        return Transcript(DEFAULT_TEXT)


def test_voice_matrix_cli_reuses_runner_continues_and_writes_json_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    app, requests = _fake_voice_app()
    monkeypatch.setattr(readio_api, "Readio", lambda: app)
    _SharedMockRedux.instances = 0
    _SharedMockRedux.enters = 0
    monkeypatch.setattr(benchmark_voices, "ReduxTranscriber", _SharedMockRedux)

    status: int = benchmark_voices.main(["--work-dir", str(tmp_path / "matrix"), "--json"])

    output = tmp_path / "matrix"
    document = json.loads((output / "results.json").read_text(encoding="utf-8"))
    csv_text = (output / "results.csv").read_text(encoding="utf-8")
    assert len(requests) == 2
    assert _SharedMockRedux.instances == 1
    assert _SharedMockRedux.enters == 1
    assert [row["voice"] for row in document["results"]] == ["af_bella", "af_heart"]
    assert document["summary"]["passed"] == 1
    assert document["summary"]["failed"] == 1
    assert (output / "wav" / "001-kokoro-v1-0-af-bella.wav").is_file()
    assert "transcript" not in csv_text.casefold()
    assert DEFAULT_TEXT not in csv_text
    assert "wrong-engine" in document["results"][1]["error"]
    assert json.loads(capsys.readouterr().out)["schema"] == (
        "readio.benchmark.redux.voice-matrix.v1"
    )
    assert status == 1
