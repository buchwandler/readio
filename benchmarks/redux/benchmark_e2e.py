"""Run one explicit Readio -> WAV -> Parakeet Redux benchmark case."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from .common import (
    DEFAULT_FAIL_CER,
    DEFAULT_FAIL_WER,
    DEFAULT_PASS_CER,
    DEFAULT_PASS_WER,
    DEFAULT_REDUX_MODEL,
    SHORT_TAIL_CASE,
    BenchmarkResult,
    ReduxTranscriber,
    default_case,
    environment_metadata,
    parse_engine_options,
    run_case,
    summarize_short_tail,
    to_jsonable,
    write_json,
)

DEFAULT_LANGUAGE = "en-us"
DEFAULT_ENGINE = "kokoro"
DEFAULT_MODEL = "v1.0"
DEFAULT_VOICE = "kokoro:v1.0/af_sarah"


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
    """Execute the exact default pipeline used by the CLI and opt-in pytest test."""
    args = argparse.Namespace(
        language=language,
        engine=engine,
        model=model,
        voice=voice,
        redux_model=redux_model,
        redux_device=redux_device,
    )
    started = perf_counter()
    failure_stage = "configuration"
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        from readio.api import Readio

        app = Readio()
        failure_stage = "redux_load"
        with ReduxTranscriber(model=redux_model, device=redux_device) as redux:
            redux_load_seconds = redux.load_seconds
            result = run_case(
                app=app,
                case=default_case(),
                language=language,
                engine=engine,
                model=model,
                voice=voice,
                project_dir=work_dir / "project.readio",
                source_path=work_dir / "source.txt",
                engine_device=device,
                engine_options=engine_options,
                redux=redux,
                pass_wer=pass_wer,
                pass_cer=pass_cer,
                fail_wer=fail_wer,
                fail_cer=fail_cer,
            )
        return replace(
            result,
            total_seconds=perf_counter() - started,
            redux_load_seconds=redux_load_seconds,
        )
    except Exception as error:  # noqa: BLE001 - return diagnostics to CLI and opt-in pytest caller
        return _failure_result(
            args=args,
            error=error,
            failure_stage=failure_stage,
            total_seconds=perf_counter() - started,
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
    """Run the exact short-tail regression repeatedly, reusing one Redux model."""
    if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    work_dir.mkdir(parents=True, exist_ok=True)
    from readio.api import Readio

    app = Readio()
    with ReduxTranscriber() as redux:
        results = [
            run_case(
                app=app,
                case=SHORT_TAIL_CASE,
                language=language,
                engine=engine,
                model=model,
                voice=voice,
                project_dir=work_dir / f"run-{ordinal:03d}.readio",
                source_path=work_dir / f"run-{ordinal:03d}.txt",
                engine_device=device,
                engine_options=engine_options,
                redux=redux,
            )
            for ordinal in range(1, repetitions + 1)
        ]
    report: dict[str, object] = {
        "schema": "readio.benchmark.redux.short-tail.v1",
        "engine": engine,
        "model": model,
        "voice": voice,
        **summarize_short_tail(results),
    }
    return report


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
