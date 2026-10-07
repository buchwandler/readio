"""Run one explicit Readio -> WAV -> Parakeet Redux benchmark case."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from readio.verification.cases import POCKET_SHORT_TAIL_CASE

from .common import (
    DEFAULT_FAIL_CER,
    DEFAULT_FAIL_WER,
    DEFAULT_PASS_CER,
    DEFAULT_PASS_WER,
    DEFAULT_REDUX_MODEL,
    BenchmarkCase,
    BenchmarkResult,
    _safe_engine_options,
    default_case,
    environment_metadata,
    normalize_text,
    parse_engine_options,
    to_jsonable,
    verify,
    write_json,
)

DEFAULT_LANGUAGE = "en-us"
DEFAULT_ENGINE = "kokoro"
DEFAULT_MODEL = "v1.0"
DEFAULT_VOICE = "kokoro:v1.0/af_sarah"


SHORT_TAIL_CASE = BenchmarkCase(
    name="pocket-short-tail",
    text=POCKET_SHORT_TAIL_CASE.text,
    expected_text=POCKET_SHORT_TAIL_CASE.expected_text,
)


def numeric_summary(values: Sequence[float | int | None]) -> dict[str, float] | None:
    samples = sorted(float(value) for value in values if value is not None and math.isfinite(value))
    if not samples:
        return None
    return {
        "mean": sum(samples) / len(samples),
        "median": (samples[(len(samples) - 1) // 2] + samples[len(samples) // 2]) / 2,
        "min": samples[0],
        "max": samples[-1],
    }


def summarize_short_tail(results: Sequence[BenchmarkResult]) -> dict[str, Any]:
    """Summarize repeated exact short-tail attempts without relaxing pass thresholds."""
    attempts: list[dict[str, Any]] = []
    wers: list[float] = []
    cers: list[float] = []
    durations: list[float] = []
    pass_count = 0
    for ordinal, result in enumerate(results, start=1):
        verification = result.verification
        terminal_complete = verification is not None and normalize_text(
            verification.transcript
        ) == normalize_text(SHORT_TAIL_CASE.reference_text)
        passed = result.status == "pass" and terminal_complete
        pass_count += int(passed)
        if verification is not None:
            wers.append(verification.wer)
            cers.append(verification.cer)
        durations.append(result.total_seconds)
        attempts.append(
            {
                "run": ordinal,
                **result.to_dict(),
                "terminal_complete": terminal_complete,
                "accepted": passed,
            }
        )
    wer_summary = numeric_summary(wers) or {}
    cer_summary = numeric_summary(cers) or {}
    duration_summary = numeric_summary(durations) or {}
    return {
        "case": SHORT_TAIL_CASE.name,
        "text": SHORT_TAIL_CASE.text,
        "runs": len(results),
        "pass_count": pass_count,
        "fail_count": len(results) - pass_count,
        "wer": {"median": wer_summary.get("median"), "worst": max(wers, default=None)},
        "cer": {"median": cer_summary.get("median"), "worst": max(cers, default=None)},
        "duration_seconds": {key: duration_summary.get(key) for key in ("min", "median", "max")},
        "attempts": attempts,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", default=DEFAULT_LANGUAGE)
    parser.add_argument("--engine", default=DEFAULT_ENGINE)
    parser.add_argument(
        "--case",
        choices=("default", "pocket-short-tail"),
        default="default",
        help="benchmark case (pocket-short-tail is the observed terminal-truncation regression)",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=20,
        help="attempt count for --case pocket-short-tail (default: 20)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    parser.add_argument(
        "--device",
        help="optional Readio engine device passed through the public synthesis request",
    )
    parser.add_argument(
        "--engine-option",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="repeatable engine-specific option; scalar values are coerced conservatively",
    )
    parser.add_argument(
        "--redux-device", default="cpu", help="Parakeet Redux device (default: cpu)"
    )
    parser.add_argument("--redux-model", default=DEFAULT_REDUX_MODEL)
    parser.add_argument(
        "--work-dir", type=Path, help="artifact directory (must use a fresh project path)"
    )
    parser.add_argument(
        "--keep", action="store_true", help="retain project and artifacts (always retained in v1)"
    )
    parser.add_argument("--json", action="store_true", help="print the result JSON to stdout")
    parser.add_argument("--strict", action="store_true", help="return nonzero for a review result")
    parser.add_argument("--pass-wer", type=float, default=DEFAULT_PASS_WER)
    parser.add_argument("--pass-cer", type=float, default=DEFAULT_PASS_CER)
    parser.add_argument("--fail-wer", type=float, default=DEFAULT_FAIL_WER)
    parser.add_argument("--fail-cer", type=float, default=DEFAULT_FAIL_CER)
    return parser


def _work_dir(requested: Path | None) -> Path:
    if requested is not None:
        return requested
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return Path("benchmark-output") / f"redux-e2e-{stamp}"


def _threshold_error(args: argparse.Namespace) -> str | None:
    values = (args.pass_wer, args.pass_cer, args.fail_wer, args.fail_cer)
    if any(not math.isfinite(value) or value < 0 or value > 1 for value in values):
        return "WER/CER thresholds must be between 0 and 1"
    if args.pass_wer > args.fail_wer or args.pass_cer > args.fail_cer:
        return "pass thresholds must not exceed fail thresholds"
    return None


def _failure_result(
    *,
    args: argparse.Namespace,
    error: Exception,
    failure_stage: str,
    total_seconds: float,
) -> BenchmarkResult:
    return BenchmarkResult(
        status="fail",
        case=default_case().name,
        language=args.language,
        engine=args.engine,
        model=args.model,
        voice=args.voice,
        redux_model=args.redux_model,
        redux_device=args.redux_device,
        total_seconds=total_seconds,
        environment=environment_metadata(
            engine=args.engine,
            redux_model=args.redux_model,
            redux_device=args.redux_device,
        ),
        error=f"{type(error).__name__}: {error}",
        failure_stage=failure_stage,
    )


def _number(data: Mapping[str, Any], key: str) -> float | None:
    value = data.get(key)
    return float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else None


def _api_benchmark_result(
    document: Mapping[str, Any],
    attempt: Mapping[str, Any],
    *,
    case_name: str,
    expected_text: str,
    language: str,
    engine: str,
    model: str,
    voice: str,
    engine_options: Mapping[str, object] | None,
    redux_model: str,
    redux_device: str,
    pass_wer: float = DEFAULT_PASS_WER,
    pass_cer: float = DEFAULT_PASS_CER,
    fail_wer: float = DEFAULT_FAIL_WER,
    fail_cer: float = DEFAULT_FAIL_CER,
) -> BenchmarkResult:
    resolved = attempt.get("resolved") if isinstance(attempt.get("resolved"), Mapping) else {}
    planning = attempt.get("planning") if isinstance(attempt.get("planning"), Mapping) else {}
    synthesis = attempt.get("synthesis") if isinstance(attempt.get("synthesis"), Mapping) else {}
    composition = (
        attempt.get("composition") if isinstance(attempt.get("composition"), Mapping) else {}
    )
    verification = (
        attempt.get("verification") if isinstance(attempt.get("verification"), Mapping) else {}
    )
    timings = attempt.get("timings") if isinstance(attempt.get("timings"), Mapping) else {}
    redux_metadata = (
        document.get("verification") if isinstance(document.get("verification"), Mapping) else {}
    )
    error_data = attempt.get("error") if isinstance(attempt.get("error"), Mapping) else {}
    transcript = verification.get("transcript")
    verified = (
        verify(
            expected=expected_text,
            transcript=transcript,
            pass_wer=pass_wer,
            pass_cer=pass_cer,
            fail_wer=fail_wer,
            fail_cer=fail_cer,
        )
        if isinstance(transcript, str)
        else None
    )
    status_value = str(attempt.get("overall_status", document.get("overall_status", "fail")))
    status = status_value if status_value in {"pass", "review", "fail"} else "fail"
    composition_seconds = _number(timings, "composition_seconds")
    synthesis_seconds = _number(timings, "synthesis_seconds")
    redux_seconds = _number(timings, "redux_seconds")
    audio_seconds = _number(composition, "audio_seconds")
    total_seconds = _number(attempt, "seconds")
    api_attempts = document.get("attempts")
    if isinstance(api_attempts, list) and len(api_attempts) == 1:
        total_seconds = _number(document.get("timings", {}), "total_seconds") or total_seconds
    if total_seconds is None:
        total_seconds = 0.0
    plan_attempt = planning.get("attempt_id")
    loudness = composition.get("loudness")
    return BenchmarkResult(
        status=status,
        case=case_name,
        language=language,
        engine=str(resolved.get("engine", engine)),
        model=str(resolved.get("model", model)),
        engine_options=_safe_engine_options(engine_options or {}),
        voice=str(resolved.get("voice", voice)),
        voice_ref=str(resolved.get("voice_ref", voice))
        if resolved.get("voice_ref", voice)
        else None,
        resolved_language=str(resolved["language"]) if resolved.get("language") else None,
        resolved_engine=str(resolved["engine"]) if resolved.get("engine") else None,
        resolved_model=str(resolved["model"]) if resolved.get("model") else None,
        resolved_voice=str(resolved["voice"]) if resolved.get("voice") else None,
        project_path=str(attempt["project_path"]) if attempt.get("project_path") else None,
        plan_ids=(str(plan_attempt),) if plan_attempt else (),
        scope_count=int(planning["scope_count"])
        if isinstance(planning.get("scope_count"), int)
        else None,
        planned_units=int(planning["planned_units"])
        if isinstance(planning.get("planned_units"), int)
        else None,
        synthesis_profile_id=str(synthesis["profile_id"]) if synthesis.get("profile_id") else None,
        selected_units=int(synthesis["selected_units"])
        if isinstance(synthesis.get("selected_units"), int)
        else None,
        rendered_units=int(synthesis["rendered_units"])
        if isinstance(synthesis.get("rendered_units"), int)
        else None,
        reused_units=int(synthesis["reused_units"])
        if isinstance(synthesis.get("reused_units"), int)
        else None,
        active_synthesis=bool(synthesis["activated"])
        if isinstance(synthesis.get("activated"), bool)
        else None,
        composition_id=str(composition["composition_id"])
        if composition.get("composition_id")
        else None,
        composition_items=int(composition["items"])
        if isinstance(composition.get("items"), int)
        else None,
        loudness=loudness if isinstance(loudness, Mapping) else None,
        wav=str(composition["wav"]) if composition.get("wav") else None,
        wav_sha256=str(composition["wav_sha256"]) if composition.get("wav_sha256") else None,
        input_sha256=str(attempt["source_sha256"]) if attempt.get("source_sha256") else None,
        audio_seconds=audio_seconds,
        sample_rate=int(composition["sample_rate"])
        if isinstance(composition.get("sample_rate"), int)
        else None,
        channels=int(composition["channels"])
        if isinstance(composition.get("channels"), int)
        else None,
        project_seconds=_number(timings, "project_seconds"),
        resolution_seconds=_number(timings, "resolution_seconds"),
        plan_seconds=_number(timings, "planning_seconds"),
        synthesis_seconds=synthesis_seconds,
        composition_seconds=composition_seconds,
        redux_load_seconds=_number(document.get("timings", {}), "backend_load_seconds"),
        redux_seconds=redux_seconds,
        total_seconds=total_seconds,
        synthesis_x_real_time=(
            audio_seconds / synthesis_seconds
            if audio_seconds is not None and synthesis_seconds
            else None
        ),
        redux_x_real_time=(
            audio_seconds / redux_seconds if audio_seconds is not None and redux_seconds else None
        ),
        redux_model=redux_model,
        redux_revision=str(redux_metadata["backend_package_version"])
        if redux_metadata.get("backend_package_version")
        else None,
        redux_device=redux_device,
        verification=verified,
        environment=environment_metadata(
            engine=engine, redux_model=redux_model, redux_device=redux_device
        ),
        error=str(error_data.get("message")) if error_data.get("message") else None,
        failure_stage=str(attempt["failure_stage"]) if attempt.get("failure_stage") else None,
    )


def _run_api_case(
    *,
    work_dir: Path,
    case: str,
    case_name: str,
    expected_text: str,
    language: str,
    engine: str,
    model: str,
    voice: str,
    device: str | None,
    engine_options: Mapping[str, object] | None,
    redux_device: str,
    redux_model: str,
    repetitions: int,
    pass_wer: float,
    pass_cer: float,
    fail_wer: float,
    fail_cer: float,
) -> tuple[Mapping[str, Any], list[BenchmarkResult]]:
    from readio.api import (
        CompositionOptions,
        ProjectPlanOptions,
        Readio,
        SelfTestRequest,
        SynthesisRequest,
        VerificationOptions,
    )

    options = dict(engine_options or {})
    if device is not None:
        options.setdefault("device", device)
    request = SelfTestRequest(
        case=case,
        synthesis=SynthesisRequest(
            language=language,
            engine=engine,
            model=model,
            voice=voice,
            engine_options=options,
        ),
        planning=ProjectPlanOptions(),
        composition=CompositionOptions(sample_rate=16_000),
        verification=VerificationOptions(model=redux_model, device=redux_device),
        repetitions=repetitions,
        pass_wer=pass_wer,
        pass_cer=pass_cer,
        fail_wer=fail_wer,
        fail_cer=fail_cer,
        output=work_dir,
    )
    result = Readio().verification.run_e2e(request)
    document = result.to_dict()
    rows = [
        _api_benchmark_result(
            document,
            attempt,
            case_name=case_name,
            expected_text=expected_text,
            language=language,
            engine=engine,
            model=model,
            voice=voice,
            engine_options=options,
            redux_model=redux_model,
            redux_device=redux_device,
            pass_wer=pass_wer,
            pass_cer=pass_cer,
            fail_wer=fail_wer,
            fail_cer=fail_cer,
        )
        for attempt in result.attempts
    ]
    if not rows:
        rows = [
            _failure_result(
                args=argparse.Namespace(
                    language=language,
                    engine=engine,
                    model=model,
                    voice=voice,
                    redux_model=redux_model,
                    redux_device=redux_device,
                ),
                error=RuntimeError(
                    str((result.error or {}).get("message", "verification produced no attempts"))
                ),
                failure_stage=result.failure_stage or "verification",
                total_seconds=float(result.timings.get("total_seconds", 0.0)),
            )
        ]
    return document, rows


def run_default_e2e(
    work_dir: Path,
    *,
    language: str = DEFAULT_LANGUAGE,
    engine: str = DEFAULT_ENGINE,
    model: str = DEFAULT_MODEL,
    voice: str = DEFAULT_VOICE,
    device: str | None = None,
    engine_options: Mapping[str, object] | None = None,
    redux_device: str = "cpu",
    redux_model: str = DEFAULT_REDUX_MODEL,
    pass_wer: float = DEFAULT_PASS_WER,
    pass_cer: float = DEFAULT_PASS_CER,
    fail_wer: float = DEFAULT_FAIL_WER,
    fail_cer: float = DEFAULT_FAIL_CER,
) -> BenchmarkResult:
    """Compatibility wrapper over the packaged verification API."""
    started = perf_counter()
    try:
        _, results = _run_api_case(
            work_dir=work_dir,
            case="readio-e2e-en-v1",
            case_name=default_case().name,
            expected_text=default_case().reference_text,
            language=language,
            engine=engine,
            model=model,
            voice=voice,
            device=device,
            engine_options=engine_options,
            redux_device=redux_device,
            redux_model=redux_model,
            repetitions=1,
            pass_wer=pass_wer,
            pass_cer=pass_cer,
            fail_wer=fail_wer,
            fail_cer=fail_cer,
        )
        return results[0]
    except Exception as error:  # noqa: BLE001 - return diagnostics to CLI and opt-in pytest caller
        return _failure_result(
            args=argparse.Namespace(
                language=language,
                engine=engine,
                model=model,
                voice=voice,
                redux_model=redux_model,
                redux_device=redux_device,
            ),
            error=error,
            failure_stage="verification",
            total_seconds=max(0.0, perf_counter() - started),
        )


def run_repeated_short_tail(
    work_dir: Path,
    *,
    repetitions: int = 20,
    language: str = "en",
    engine: str = "pocket",
    model: str = "english_2026-04",
    voice: str = "pocket:english_2026-04/alba",
    device: str | None = None,
    engine_options: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Run the packaged short-tail case with one Redux session and fresh attempts."""
    if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    work_dir.mkdir(parents=True, exist_ok=True)
    document, results = _run_api_case(
        work_dir=work_dir,
        case="pocket-short-tail-v1",
        case_name=SHORT_TAIL_CASE.name,
        expected_text=SHORT_TAIL_CASE.reference_text,
        language=language,
        engine=engine,
        model=model,
        voice=voice,
        device=device,
        engine_options=engine_options,
        redux_device="cpu",
        redux_model=DEFAULT_REDUX_MODEL,
        repetitions=repetitions,
        pass_wer=DEFAULT_PASS_WER,
        pass_cer=DEFAULT_PASS_CER,
        fail_wer=DEFAULT_FAIL_WER,
        fail_cer=DEFAULT_FAIL_CER,
    )
    return {
        "schema": "readio.benchmark.redux.short-tail.v1",
        "engine": engine,
        "model": model,
        "voice": voice,
        **summarize_short_tail(results),
        "verification_output": str(document.get("output", work_dir)),
    }


def _human_report(
    result: BenchmarkResult, result_path: Path, *, redux_load_seconds: float | None
) -> str:
    verification = result.verification
    rows = [
        "Readio Redux E2E Benchmark",
        "",
        f"Result: {result.status.upper()}",
        "",
        "Target",
        f"  Language: {result.language}",
        f"  Engine:   {result.engine}",
        f"  Model:    {result.model}",
        f"  Voice:    {result.voice}",
        f"  Ref:      {result.voice_ref or result.voice}",
        "",
        "Pipeline",
        f"  Project:    {result.project_seconds or 0.0:.3f} s",
        f"  Resolution: {result.resolution_seconds or 0.0:.3f} s",
        (
            f"  Plan:       {result.plan_seconds or 0.0:.3f} s  "
            f"{result.scope_count or 0} scopes / {result.planned_units or 0} units"
        ),
        (
            f"  Synthesis:  {result.synthesis_seconds or 0.0:.3f} s  "
            f"{result.rendered_units or 0} rendered / {result.reused_units or 0} reused"
        ),
        f"  Compose:    {result.composition_seconds or 0.0:.3f} s",
        f"  Audio:      {result.audio_seconds or 0.0:.3f} s",
        f"  TTS speed:  {result.synthesis_x_real_time or 0.0:.2f} x realtime",
        "",
        "Redux",
        f"  Model:      {result.redux_model}",
        f"  Load:       {redux_load_seconds or result.redux_load_seconds or 0.0:.3f} s",
        f"  ASR:        {result.redux_seconds or 0.0:.3f} s",
        f"  ASR speed:  {result.redux_x_real_time or 0.0:.2f} x realtime",
        "",
        "Verification",
    ]
    if verification is not None:
        rows.extend(
            [
                f"  WER: {verification.wer:.4f}",
                f"  CER: {verification.cer:.4f}",
                "  Expected:",
                f"    {verification.expected}",
                "  Transcript:",
                f"    {verification.transcript}",
            ]
        )
    if result.error:
        rows.extend(["", f"Error ({result.failure_stage or 'unknown stage'}): {result.error}"])
    rows.extend(
        [
            "",
            "Artifacts",
            f"  WAV:    {result.wav or '(not produced)'}",
            f"  Source: {Path(result.project_path).parent / 'source.txt' if result.project_path else '(not produced)'}",
            f"  JSON:   {result_path}",
            f"  Total:  {result.total_seconds:.3f} s",
        ]
    )
    return "\n".join(rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        engine_options = parse_engine_options(args.engine_option)
    except ValueError as error:
        parser.error(str(error))
    threshold_error = _threshold_error(args)
    if threshold_error:
        parser.error(threshold_error)

    if args.case == "pocket-short-tail":
        if args.repetitions < 1:
            parser.error("--repetitions must be at least 1")
        engine = "pocket" if args.engine == DEFAULT_ENGINE else args.engine
        model = "english_2026-04" if args.model == DEFAULT_MODEL else args.model
        voice = "pocket:english_2026-04/alba" if args.voice == DEFAULT_VOICE else args.voice
        work_dir = _work_dir(args.work_dir)
        try:
            report = run_repeated_short_tail(
                work_dir,
                repetitions=args.repetitions,
                language=args.language,
                engine=engine,
                model=model,
                voice=voice,
                device=args.device,
                engine_options=engine_options,
            )
        except Exception as error:  # noqa: BLE001 - persist failure of a diagnostic run
            report = {
                "schema": "readio.benchmark.redux.short-tail.v1",
                "case": SHORT_TAIL_CASE.name,
                "text": SHORT_TAIL_CASE.text,
                "runs": 0,
                "pass_count": 0,
                "fail_count": args.repetitions,
                "error": f"{type(error).__name__}: {error}",
            }
        result_path = work_dir / "result.json"
        write_json(result_path, report)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(
                f"Pocket short-tail benchmark: {report.get('pass_count', 0)}/"
                f"{report.get('runs', 0)} complete; results: {result_path}"
            )
        return 1 if report.get("fail_count", 0) else 0

    work_dir = _work_dir(args.work_dir)
    result_path = work_dir / "result.json"
    result = run_default_e2e(
        work_dir,
        language=args.language,
        engine=args.engine,
        model=args.model,
        voice=args.voice,
        device=args.device,
        engine_options=engine_options,
        redux_device=args.redux_device,
        redux_model=args.redux_model,
        pass_wer=args.pass_wer,
        pass_cer=args.pass_cer,
        fail_wer=args.fail_wer,
        fail_cer=args.fail_cer,
    )
    redux_load_seconds = result.redux_load_seconds

    try:
        write_json(result_path, result)
    except Exception as error:  # noqa: BLE001 - report result-file write errors without masking benchmark output
        print(f"Could not write benchmark result {result_path}: {error}", file=sys.stderr)

    if args.json:
        print(json.dumps(to_jsonable(result), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_human_report(result, result_path, redux_load_seconds=redux_load_seconds))

    if result.status == "fail":
        if result.failure_stage in {
            "configuration",
            "source_creation",
            "project_creation",
            "preflight",
            "redux_load",
            "readio_initialization",
        }:
            return 2
        return 1
    if result.status == "review" and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
