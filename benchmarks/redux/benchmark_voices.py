"""Measure every runnable voice for one Readio engine/model/language target."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .benchmark_e2e import (
    DEFAULT_ENGINE,
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
    _threshold_error,
)
from .common import (
    DEFAULT_FAIL_CER,
    DEFAULT_FAIL_WER,
    DEFAULT_PASS_CER,
    DEFAULT_PASS_WER,
    DEFAULT_REDUX_MODEL,
    VOICE_MATRIX_SCHEMA,
    BenchmarkResult,
    ReduxTranscriber,
    aggregate_results,
    default_case,
    environment_metadata,
    filter_voices,
    parse_engine_options,
    run_case,
    safe_name,
    sha256_file,
    to_jsonable,
    voice_wav_name,
    write_json,
)

CSV_COLUMNS = (
    "voice",
    "voice_ref",
    "engine",
    "model",
    "language",
    "status",
    "wer",
    "cer",
    "audio_seconds",
    "plan_seconds",
    "synthesis_seconds",
    "composition_seconds",
    "redux_seconds",
    "synthesis_x_real_time",
    "redux_x_real_time",
    "rendered_units",
    "reused_units",
    "wav_sha256",
    "wav_path",
    "error",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", default=DEFAULT_LANGUAGE)
    parser.add_argument("--engine", default=DEFAULT_ENGINE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--device", help="optional Readio engine device passed through the synthesis request"
    )
    parser.add_argument(
        "--engine-option",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="repeatable engine-specific option; scalar values are coerced conservatively",
    )
    parser.add_argument("--redux-device", default="cpu")
    parser.add_argument("--redux-model", default=DEFAULT_REDUX_MODEL)
    parser.add_argument("--work-dir", type=Path, help="matrix artifact directory")
    parser.add_argument(
        "--keep", action="store_true", help="retain all per-voice projects and artifacts"
    )
    parser.add_argument("--include-experimental", action="store_true")
    parser.add_argument(
        "--json", action="store_true", help="print results JSON instead of the human report"
    )
    parser.add_argument(
        "--strict", action="store_true", help="return nonzero when any voice needs review"
    )
    parser.add_argument("--pass-wer", type=float, default=DEFAULT_PASS_WER)
    parser.add_argument("--pass-cer", type=float, default=DEFAULT_PASS_CER)
    parser.add_argument("--fail-wer", type=float, default=DEFAULT_FAIL_WER)
    parser.add_argument("--fail-cer", type=float, default=DEFAULT_FAIL_CER)
    return parser


def _work_dir(requested: Path | None) -> Path:
    if requested is not None:
        return requested
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return Path("benchmark-output") / f"redux-voices-{stamp}"


def _write_csv(path: Path, results: Sequence[BenchmarkResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for result in results:
            verification = result.verification
            writer.writerow(
                {
                    **result.to_dict(),
                    "wer": verification.wer if verification else "",
                    "cer": verification.cer if verification else "",
                }
            )


def _matrix_document(
    *,
    args: argparse.Namespace,
    results: Sequence[BenchmarkResult],
    redux_load_seconds: float | None,
    error: str | None = None,
) -> dict[str, Any]:
    summary = aggregate_results(results)
    summary["redux_load_seconds"] = redux_load_seconds
    document: dict[str, Any] = {
        "schema": VOICE_MATRIX_SCHEMA,
        "environment": environment_metadata(
            engine=args.engine,
            redux_model=args.redux_model,
            redux_device=args.redux_device,
        ),
        "redux_model": args.redux_model,
        "redux_revision": None,
        "redux_device": args.redux_device,
        "redux_load_seconds": redux_load_seconds,
        "query": {"language": args.language, "engine": args.engine, "model": args.model},
        "case": {
            "name": default_case().name,
            "text": default_case().text,
            "expected_text": default_case().expected_text,
            "reference_text": default_case().reference_text,
        },
        "summary": summary,
        "results": list(results),
    }
    if error:
        document["error"] = error
    return to_jsonable(document)


def _write_reports(
    work_dir: Path,
    args: argparse.Namespace,
    results: Sequence[BenchmarkResult],
    redux_load_seconds: float | None,
    error: str | None = None,
) -> dict[str, Any]:
    document = _matrix_document(
        args=args,
        results=results,
        redux_load_seconds=redux_load_seconds,
        error=error,
    )
    write_json(work_dir / "results.json", document)
    _write_csv(work_dir / "results.csv", results)
    return document


def _metric(value: float | None, *, precision: int = 3) -> str:
    return "-" if value is None or not math.isfinite(value) else f"{value:.{precision}f}"


def _human_report(
    *,
    args: argparse.Namespace,
    voices: Sequence[Any],
    results: Sequence[BenchmarkResult],
    document: dict[str, Any],
    work_dir: Path,
) -> str:
    query = document["query"]
    summary = document["summary"]
    rows = [
        "Readio Voice Benchmark",
        f"Engine: {query['engine']}",
        f"Model: {query['model']}",
        f"Language: {query['language']}",
        f"Redux model: {args.redux_model} ({args.redux_device})",
        f"Voices: {len(voices)}",
        f"Experimental included: {'yes' if args.include_experimental else 'no'}",
        "",
        "VOICE                 AUDIO   SYNTH    XRT     ASR      WER     CER    RESULT",
        "--------------------  ------  -------  ------  -------  ------  ------  ------",
    ]
    for result in results:
        verification = result.verification
        voice_label = result.voice[:20]
        rows.append(
            f"{voice_label:<20}  "
            f"{_metric(result.audio_seconds, precision=1):>5}s  "
            f"{_metric(result.synthesis_seconds, precision=2):>6}s  "
            f"{_metric(result.synthesis_x_real_time, precision=2):>5}x  "
            f"{_metric(result.redux_seconds, precision=2):>6}s  "
            f"{_metric(verification.wer if verification else None):>6}  "
            f"{_metric(verification.cer if verification else None):>6}  "
            f"{result.status.upper():>6}"
        )
    rows.extend(
        [
            "",
            "Summary",
            f"  Voices: {summary['voice_count']}",
            f"  Passed: {summary['passed']}",
            f"  Review: {summary['review']}",
            f"  Failed: {summary['failed']}",
        ]
    )
    for metric in ("wer", "cer", "synthesis_x_real_time", "redux_x_real_time"):
        stats = summary[metric]
        if stats is not None:
            label = {
                "wer": "WER",
                "cer": "CER",
                "synthesis_x_real_time": "TTS xRT",
                "redux_x_real_time": "ASR xRT",
            }[metric]
            rows.append(
                f"  Mean {label}: {_metric(stats['mean'])}; median: {_metric(stats['median'])}; "
                f"min: {_metric(stats['min'])}; max: {_metric(stats['max'])}"
            )
    rows.append(f"  Redux load: {_metric(document['redux_load_seconds'])} s")
    if summary["slowest_synthesis"]:
        slowest = summary["slowest_synthesis"]
        rows.extend(
            [
                "",
                "Slowest synthesis:",
                f"  {slowest['voice']}: {slowest['synthesis_seconds']:.3f} s",
            ]
        )
    if summary["highest_wer"]:
        worst = summary["highest_wer"]
        rows.extend(["", "Highest WER:", f"  {worst['voice']}: {worst['wer']:.4f}"])
    failures = [result for result in results if result.status == "fail"]
    if failures:
        rows.extend(["", "Failure details"])
        for result in failures:
            detail = result.error or "semantic/audio verification failed"
            if result.verification is not None:
                detail += (
                    f"; WER={result.verification.wer:.4f}, CER={result.verification.cer:.4f}"
                    f"; transcript={result.verification.transcript}"
                )
            rows.append(f"  {result.voice}: {detail}")
    if document.get("error"):
        rows.extend(["", f"Benchmark error: {document['error']}"])
    rows.extend(
        [
            "",
            "Artifacts",
            f"  JSON: {work_dir / 'results.json'}",
            f"  CSV:  {work_dir / 'results.csv'}",
            f"  WAVs: {work_dir / 'wav'}",
            f"  Projects: {work_dir / 'projects'}",
        ]
    )
    return "\n".join(rows)


def _failed_voice_result(
    args: argparse.Namespace,
    voice: Any,
    error: Exception,
    *,
    failure_stage: str = "voice_runner",
) -> BenchmarkResult:
    voice_id = str(getattr(voice, "id", voice))
    voice_ref = getattr(voice, "ref", None)
    return BenchmarkResult(
        status="fail",
        case=default_case().name,
        language=args.language,
        engine=getattr(voice, "engine", args.engine),
        model=getattr(voice, "target_id", args.model),
        voice=voice_id,
        voice_ref=voice_ref,
        redux_model=args.redux_model,
        redux_device=args.redux_device,
        environment=environment_metadata(
            engine=args.engine,
            redux_model=args.redux_model,
            redux_device=args.redux_device,
        ),
        error=f"{type(error).__name__}: {error}",
        failure_stage=failure_stage,
    )


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

    work_dir = _work_dir(args.work_dir)
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f"Cannot create benchmark output directory {work_dir}: {error}", file=sys.stderr)
        return 2

    try:
        from readio.api import Readio, VoiceQuery

        app = Readio()
        all_voices = app.catalog.voices(
            VoiceQuery(language=args.language, engine=args.engine, model=args.model)
        )
        voices = filter_voices(all_voices, include_experimental=args.include_experimental)
        write_json(
            work_dir / "voices.json",
            {
                "schema": VOICE_MATRIX_SCHEMA,
                "query": {"language": args.language, "engine": args.engine, "model": args.model},
                "voices": all_voices,
                "selected_voice_refs": [getattr(voice, "ref", None) for voice in voices],
            },
        )
    except Exception as error:  # noqa: BLE001 - persist discovery/preflight errors as a matrix report
        message = f"{type(error).__name__}: {error}"
        document = _write_reports(work_dir, args, (), None, message)
        if args.json:
            print(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(
                f"Readio Voice Benchmark\n\nBenchmark error: {message}\nJSON: {work_dir / 'results.json'}"
            )
        return 2

    if not voices:
        message = "catalog query found no runnable voices for the requested target"
        document = _write_reports(work_dir, args, (), None, message)
        if args.json:
            print(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(
                _human_report(
                    args=args, voices=voices, results=(), document=document, work_dir=work_dir
                )
            )
        return 2

    projects_dir = work_dir / "projects"
    wav_dir = work_dir / "wav"
    source_path = work_dir / "source.txt"
    projects_dir.mkdir(parents=True, exist_ok=True)
    wav_dir.mkdir(parents=True, exist_ok=True)
    results: list[BenchmarkResult] = []
    redux_load_seconds: float | None = None
    fatal_error: str | None = None
    try:
        with ReduxTranscriber(model=args.redux_model, device=args.redux_device) as redux:
            redux_load_seconds = redux.load_seconds
            for ordinal, voice_info in enumerate(voices, start=1):
                voice_id = str(voice_info.id)
                voice_engine = str(getattr(voice_info, "engine", args.engine))
                target_id = str(getattr(voice_info, "target_id", args.model))
                voice_ref = getattr(voice_info, "ref", None) or voice_id
                project_dir = projects_dir / f"{ordinal:03d}-{safe_name(voice_id)}.readio"
                print(f"Running voice {ordinal}/{len(voices)}: {voice_id}", file=sys.stderr)
                try:
                    result = run_case(
                        app=app,
                        case=default_case(),
                        language=args.language,
                        engine=voice_engine,
                        model=target_id,
                        voice=voice_ref,
                        project_dir=project_dir,
                        source_path=source_path,
                        engine_device=args.device,
                        engine_options=engine_options,
                        redux=redux,
                        pass_wer=args.pass_wer,
                        pass_cer=args.pass_cer,
                        fail_wer=args.fail_wer,
                        fail_cer=args.fail_cer,
                    )
                except Exception as error:  # noqa: BLE001 - isolate one voice and continue the matrix
                    result = _failed_voice_result(args, voice_info, error)
                else:
                    result = replace(
                        result, voice=voice_id, voice_ref=result.voice_ref or voice_ref
                    )
                    if result.wav and Path(result.wav).is_file():
                        output_wav = wav_dir / voice_wav_name(
                            ordinal, voice_engine, target_id, voice_id
                        )
                        try:
                            shutil.copy2(result.wav, output_wav)
                            result = replace(
                                result,
                                wav=str(output_wav),
                                wav_sha256=sha256_file(output_wav),
                            )
                        except Exception as error:  # noqa: BLE001 - keep synthesis diagnostics on copy failure
                            result = replace(
                                result,
                                status="fail",
                                error=f"{type(error).__name__}: {error}",
                                failure_stage="artifact_copy",
                            )
                results.append(result)
    except Exception as error:  # noqa: BLE001 - record Redux startup/teardown errors in reports
        fatal_error = f"{type(error).__name__}: {error}"
        for voice in voices[len(results) :]:
            results.append(
                _failed_voice_result(args, voice, error, failure_stage="redux_lifecycle")
            )

    document = _write_reports(work_dir, args, results, redux_load_seconds, fatal_error)
    if args.json:
        print(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            _human_report(
                args=args, voices=voices, results=results, document=document, work_dir=work_dir
            )
        )

    failed = any(result.status == "fail" for result in results)
    reviewed = any(result.status == "review" for result in results)
    return 1 if fatal_error or failed or (args.strict and reviewed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
