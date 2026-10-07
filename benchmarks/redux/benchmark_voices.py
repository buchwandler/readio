"""Measure every runnable voice for one Readio engine/model/language target."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .benchmark_e2e import (
    DEFAULT_ENGINE,
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
    _api_benchmark_result,
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
    default_case,
    environment_metadata,
    parse_engine_options,
    sha256_file,
    to_jsonable,
    write_json,
)


def _field(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def safe_name(value: str) -> str:
    """Return a filesystem-safe, stable lowercase component."""
    return re.sub(r"[^a-z0-9]+", "-", str(value).casefold()).strip("-") or "voice"


def voice_wav_name(ordinal: int, engine: str, model: str, voice: str) -> str:
    if ordinal < 1:
        raise ValueError("voice ordinal must be a positive integer")
    return f"{ordinal:03d}-{safe_name(engine)}-{safe_name(model)}-{safe_name(voice)}.wav"


def numeric_summary(values: Sequence[float | int | None]) -> dict[str, float | int] | None:
    samples = sorted(float(value) for value in values if value is not None and math.isfinite(value))
    if not samples:
        return None
    result: dict[str, float | int] = {
        "mean": sum(samples) / len(samples),
        "median": (samples[(len(samples) - 1) // 2] + samples[len(samples) // 2]) / 2,
        "min": samples[0],
        "max": samples[-1],
    }
    if len(samples) >= 20:
        result["p95"] = samples[math.ceil(0.95 * len(samples)) - 1]
    return result


def aggregate_results(results: Sequence[Any]) -> dict[str, Any]:
    """Create stable, transcript-free voice-matrix counts and statistics."""
    status_counts = {status: 0 for status in ("pass", "review", "fail")}
    for result in results:
        status = str(_field(result, "status", "fail")).casefold()
        if status in status_counts:
            status_counts[status] += 1

    def metric_summary(
        attribute: str, *, verification: bool = False
    ) -> dict[str, float | int] | None:
        values: list[float | None] = []
        for result in results:
            source = _field(result, "verification") if verification else result
            value = _field(source, attribute) if source is not None else None
            values.append(float(value) if isinstance(value, (int, float)) else None)
        return numeric_summary(values)

    def extreme(attribute: str, *, verification: bool = False) -> dict[str, Any] | None:
        candidates: list[tuple[float, Any]] = []
        for result in results:
            source = _field(result, "verification") if verification else result
            value = _field(source, attribute) if source is not None else None
            if isinstance(value, (int, float)) and math.isfinite(value):
                candidates.append((float(value), result))
        if not candidates:
            return None
        value, result = max(candidates, key=lambda item: item[0])
        return {"voice": _field(result, "voice"), attribute: value}

    return {
        "voice_count": len(results),
        "passed": status_counts["pass"],
        "review": status_counts["review"],
        "failed": status_counts["fail"],
        "status_counts": status_counts,
        "wer": metric_summary("wer", verification=True),
        "cer": metric_summary("cer", verification=True),
        "synthesis_seconds": metric_summary("synthesis_seconds"),
        "composition_seconds": metric_summary("composition_seconds"),
        "redux_seconds": metric_summary("redux_seconds"),
        "synthesis_x_real_time": metric_summary("synthesis_x_real_time"),
        "redux_x_real_time": metric_summary("redux_x_real_time"),
        "slowest_synthesis": extreme("synthesis_seconds"),
        "highest_wer": extreme("wer", verification=True),
    }


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
            f"  Projects: {work_dir / 'verification'}",
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

    work_dir = _work_dir(args.work_dir)
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f"Cannot create benchmark output directory {work_dir}: {error}", file=sys.stderr)
        return 2

    try:
        from readio.api import (
            CompositionOptions,
            DiscoveryOptions,
            ProjectPlanOptions,
            Readio,
            SynthesisRequest,
            VerificationOptions,
            VoiceMatrixRequest,
        )

        options = dict(engine_options)
        if args.device is not None:
            options.setdefault("device", args.device)
        matrix = Readio().verification.generate_voices(
            VoiceMatrixRequest(
                synthesis=SynthesisRequest(
                    language=args.language,
                    engine=args.engine,
                    model=args.model,
                    engine_options=options,
                ),
                planning=ProjectPlanOptions(),
                composition=CompositionOptions(sample_rate=16_000),
                verification=VerificationOptions(model=args.redux_model, device=args.redux_device),
                discovery=DiscoveryOptions(),
                include_experimental=args.include_experimental,
                pass_wer=args.pass_wer,
                pass_cer=args.pass_cer,
                fail_wer=args.fail_wer,
                fail_cer=args.fail_cer,
                output=work_dir / "verification",
            )
        )
    except Exception as error:  # noqa: BLE001 - persist discovery/preflight failures as a matrix report
        message = f"{type(error).__name__}: {error}"
        document = _write_reports(work_dir, args, (), None, message)
        if args.json:
            print(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(
                f"Readio Voice Benchmark\n\nBenchmark error: {message}\nJSON: {work_dir / 'results.json'}"
            )
        return 2

    matrix_document = matrix.to_dict()
    rows: list[BenchmarkResult] = []
    wav_dir = work_dir / "wav"
    wav_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "source.txt").write_text(default_case().text, encoding="utf-8")
    selected_voices: list[Mapping[str, Any]] = []
    for ordinal, row in enumerate(matrix.results, start=1):
        voice = row.get("voice") if isinstance(row.get("voice"), Mapping) else {}
        selected_voices.append(voice)
        synthesis = row.get("synthesis") if isinstance(row.get("synthesis"), Mapping) else {}
        timings = row.get("timings") if isinstance(row.get("timings"), Mapping) else {}
        total_seconds = sum(
            float(value)
            for key, value in timings.items()
            if key.endswith("_seconds") and isinstance(value, (int, float))
        )
        attempt: dict[str, Any] = {
            "overall_status": row.get("status", "fail"),
            "resolved": row.get("resolved", {}),
            "planning": row.get("planning", {}),
            "synthesis": synthesis,
            "composition": row.get("composition", {}),
            "verification": row.get("verification", {}),
            "timings": timings,
            "seconds": total_seconds,
            "source_sha256": row.get("source_sha256"),
            "project_path": row.get("project_path"),
            "error": row.get("error"),
            "failure_stage": row.get("failure_stage"),
        }
        result = _api_benchmark_result(
            matrix_document,
            attempt,
            case_name=default_case().name,
            expected_text=default_case().reference_text,
            language=args.language,
            engine=str(voice.get("engine", args.engine)),
            model=str(voice.get("model", args.model)),
            voice=str(voice.get("id", voice.get("ref", "unknown"))),
            engine_options=options,
            redux_model=args.redux_model,
            redux_device=args.redux_device,
            pass_wer=args.pass_wer,
            pass_cer=args.pass_cer,
            fail_wer=args.fail_wer,
            fail_cer=args.fail_cer,
        )
        if result.wav and Path(result.wav).is_file():
            output_wav = wav_dir / voice_wav_name(
                ordinal, result.engine, result.model, result.voice
            )
            try:
                shutil.copy2(result.wav, output_wav)
                result = replace(result, wav=str(output_wav), wav_sha256=sha256_file(output_wav))
            except Exception as error:  # noqa: BLE001 - retain diagnostics if an artifact copy fails
                result = replace(
                    result,
                    status="fail",
                    error=f"{type(error).__name__}: {error}",
                    failure_stage="artifact_copy",
                )
        rows.append(result)

    write_json(
        work_dir / "voices.json",
        {
            "schema": VOICE_MATRIX_SCHEMA,
            "query": matrix.query,
            "voices": selected_voices,
            "selected_voice_refs": [voice.get("ref") for voice in selected_voices],
        },
    )
    redux_metadata = matrix_document.get("redux", {})
    redux_load_seconds = (
        float(redux_metadata["load_seconds"])
        if isinstance(redux_metadata, Mapping)
        and isinstance(redux_metadata.get("load_seconds"), (int, float))
        else None
    )
    matrix_error = matrix.error.get("message") if matrix.error else None
    document = _write_reports(
        work_dir, args, rows, redux_load_seconds, str(matrix_error) if matrix_error else None
    )
    if args.json:
        print(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            _human_report(
                args=args,
                voices=selected_voices,
                results=rows,
                document=document,
                work_dir=work_dir,
            )
        )

    if matrix.error or not rows:
        return 2
    failed = any(result.status == "fail" for result in rows)
    reviewed = any(result.status == "review" for result in rows)
    return 1 if failed or (args.strict and reviewed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
