"""Public synchronous API for optional independent audio verification."""

from __future__ import annotations

import hashlib
import json
import os
import platform
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import TYPE_CHECKING, Any, cast

from ..audioio import probe_audio
from ..jsonutil import JsonValue, json_value
from ..verification.cases import VerificationCase, get_case
from ..verification.text import (
    TextVerificationResult,
    VerificationThresholds,
    normalize_text,
)
from ..verification.text import verify_text as _verify_text
from ..verification.types import TranscriptionResult
from .errors import IntegrationError, error_boundary
from .events import ReadioEvent
from .types import (
    SelfTestRequest,
    SelfTestResult,
    TimestampComparisonRequest,
    TimestampComparisonResult,
    TimestampGenerationRequest,
    TimestampGenerationResult,
    TimestampSelfTestRequest,
    TimestampSelfTestResult,
    VerificationOptions,
    VoiceMatrixRequest,
    VoiceMatrixResult,
    VoiceQuery,
)

if TYPE_CHECKING:
    from .app import Readio

E2E_SCHEMA = "readio.verification.e2e.v1"
TIMESTAMP_COMPARISON_SCHEMA = "readio.verification.timestamps.v1"
TIMESTAMP_SELFTEST_SCHEMA = "readio.verification.timestamp-selftest.v1"
TIMESTAMP_GENERATION_SCHEMA = "readio.verification.timestamp-generation.v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_options(options: Mapping[str, Any]) -> dict[str, JsonValue]:
    secret_markers = (
        "api_key",
        "apikey",
        "token",
        "secret",
        "password",
        "credential",
        "auth",
        "access_key",
        "private_key",
        "bearer",
    )
    safe = {
        str(key): "<redacted>"
        if any(marker in str(key).casefold() for marker in secret_markers)
        else value
        for key, value in options.items()
    }
    return cast(dict[str, JsonValue], json_value(safe))


def _event(
    app: Readio,
    kind: str,
    stage: str | None = None,
    *,
    operation: str = "verification.e2e",
    **details: JsonValue,
) -> None:
    handler = app.on_event
    if handler is not None:
        handler(
            ReadioEvent(
                kind=cast(Any, kind),
                operation=operation,
                stage=cast(Any, stage) if stage is not None else None,
                details=details,
            )
        )


def _bounded_issue(issue: object) -> dict[str, JsonValue]:
    fields = ("scope_id", "code", "reason", "segment_id", "unit_id", "line", "repair_safe")
    result: dict[str, JsonValue] = {}
    for name in fields:
        value = getattr(issue, name, None)
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[name] = value
    text = getattr(issue, "text", None)
    if isinstance(text, str):
        result["text"] = text[:300]
    return result


def _numeric_summary(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "min": ordered[0],
        "median": float(median(ordered)),
        "max": ordered[-1],
    }


class VerificationService:
    """Synchronous API for independent Redux verification operations."""

    def __init__(self, app: Readio) -> None:
        self._app = app

    def transcribe(
        self,
        audio: Path,
        options: VerificationOptions | None = None,
    ) -> TranscriptionResult:
        """Transcribe an audio file; the optional backend is loaded only on invocation."""
        options = options or VerificationOptions()
        try:
            from ..integrations.moondream import ReduxSession

            with (
                error_boundary(error_type=IntegrationError, code="verification.backend_error"),
                ReduxSession(model=options.model, device=options.device) as session,
            ):
                return session.transcribe(audio, timestamps=options.timestamps)
        except IntegrationError:
            raise
        except Exception as error:
            raise IntegrationError(
                f"Redux verification failed: {error}",
                code="verification.backend_error",
                details={"exception_type": type(error).__name__},
            ) from error

    def verify_text(
        self,
        audio: Path,
        expected: str,
        *,
        options: VerificationOptions | None = None,
        thresholds: VerificationThresholds | None = None,
    ) -> TextVerificationResult:
        options = options or VerificationOptions()
        thresholds = thresholds or VerificationThresholds()
        transcription = self.transcribe(audio, options)
        return _verify_text(expected=expected, transcript=transcription.text, thresholds=thresholds)

    def compare_timestamps(self, request: TimestampComparisonRequest) -> TimestampComparisonResult:
        """Transcribe one segment and compare source-aligned ASR timings to native timings."""

        from ..verification.timestamps import TimestampValidationError
        from ..verification.timestamps import compare_timestamps as compare

        info = probe_audio(request.audio)
        options = request.verification
        thresholds = VerificationThresholds(
            request.pass_wer,
            request.pass_cer,
            request.fail_wer,
            request.fail_cer,
        )
        _event(self._app, "operation.started", operation="verification.timestamps")
        _event(self._app, "stage.started", "verification", operation="verification.timestamps")
        transcription = self.transcribe(request.audio, options)
        verified = _verify_text(
            expected=request.text,
            transcript=transcription.text,
            thresholds=thresholds,
        )
        try:
            report = compare(
                text=request.text,
                transcription=transcription,
                sample_rate=info.sample_rate,
                frames=info.frames,
                native_timings=request.native_word_timings,
                context={"audio": str(request.audio)},
            )
        except TimestampValidationError as error:
            raise IntegrationError(
                f"Invalid timestamp data: {error}",
                code="verification.invalid_timestamps",
                details={"audio": str(request.audio)},
            ) from error
        result = TimestampComparisonResult(
            schema=TIMESTAMP_COMPARISON_SCHEMA,
            overall_status=cast(Any, verified.status),
            transcript_status=cast(Any, verified.status),
            wer=verified.wer,
            cer=verified.cer,
            transcript=transcription.text,
            alignment=cast(dict[str, JsonValue], json_value(report["alignment"])),
            timing_structure=cast(dict[str, JsonValue], json_value(report["timing_structure"])),
            native_comparison=cast(dict[str, JsonValue], json_value(report["native_comparison"])),
            derived_word_timings=tuple(
                cast(Mapping[str, JsonValue], json_value(row))
                for row in report["derived_word_timings"]
            ),
        )
        _event(
            self._app,
            "stage.completed",
            "verification",
            operation="verification.timestamps",
            status=verified.status,
        )
        _event(
            self._app,
            "operation.completed",
            operation="verification.timestamps",
            status=verified.status,
        )
        return result

    def run_e2e(self, request: SelfTestRequest) -> SelfTestResult:
        """Run the existing Readio project pipeline, then verify the composed master."""
        from ..integrations.moondream import ReduxSession, moondream_version

        case = get_case(request.case)
        output = request.output or _default_output_path()
        output.mkdir(parents=True, exist_ok=True)
        _event(self._app, "operation.started")
        options = request.synthesis
        engine_options = _safe_options(options.engine_options)
        requested: dict[str, JsonValue] = cast(
            dict[str, JsonValue],
            json_value(
                {
                    "synthesis": {
                        "language": options.language,
                        "engine": options.engine,
                        "model": options.model,
                        "voice": options.voice,
                        "engine_options": engine_options,
                    },
                    "repetitions": request.repetitions,
                    "require_clean_plan": request.require_clean_plan,
                    "thresholds": {
                        "pass_wer": request.pass_wer,
                        "pass_cer": request.pass_cer,
                        "fail_wer": request.fail_wer,
                        "fail_cer": request.fail_cer,
                    },
                }
            ),
        )
        results: list[dict[str, JsonValue]] = []
        overall_status = "pass"
        start_all = perf_counter()
        model_load_seconds: float | None = None
        failure_stage: str | None = None
        try:
            with ReduxSession(
                model=request.verification.model,
                device=request.verification.device,
            ) as redux:
                model_load_seconds = redux.load_seconds
                for ordinal in range(1, request.repetitions + 1):
                    attempt_dir = output / f"attempt-{ordinal:03d}"
                    attempt_dir.mkdir(parents=True, exist_ok=True)
                    attempt = self._run_one(
                        request=request,
                        case=case,
                        ordinal=ordinal,
                        attempt_dir=attempt_dir,
                        redux=redux,
                        thresholds=VerificationThresholds(
                            request.pass_wer,
                            request.pass_cer,
                            request.fail_wer,
                            request.fail_cer,
                        ),
                    )
                    results.append(attempt)
                    status = str(attempt.get("overall_status", "fail"))
                    if status == "fail":
                        overall_status = "fail"
                    elif status == "review" and overall_status == "pass":
                        overall_status = "review"
        except Exception as error:  # noqa: BLE001 - persist backend-load failure in the result artifact.
            failure_stage = "redux_load"
            overall_status = "fail"
            results.append(
                {
                    "overall_status": "fail",
                    "failure_stage": failure_stage,
                    "error": {"code": "verification.backend_error", "message": str(error)},
                }
            )
        elapsed = max(0.0, perf_counter() - start_all)
        short_tail_summary: dict[str, JsonValue] | None = None
        if case.id == "pocket-short-tail-v1":
            wer_values: list[float] = []
            cer_values: list[float] = []
            durations: list[float] = []
            pass_count = 0
            for row in results:
                verification_value = row.get("verification")
                if isinstance(verification_value, dict):
                    verification = verification_value
                    wer = verification.get("wer")
                    cer = verification.get("cer")
                    if isinstance(wer, (int, float)):
                        wer_values.append(float(wer))
                    if isinstance(cer, (int, float)):
                        cer_values.append(float(cer))
                    if (
                        row.get("overall_status") == "pass"
                        and verification.get("terminal_complete") is True
                    ):
                        pass_count += 1
                seconds = row.get("seconds")
                if isinstance(seconds, (int, float)):
                    durations.append(float(seconds))
            short_tail_summary = {
                "case": "pocket-short-tail-v1",
                "runs": len(results),
                "pass_count": pass_count,
                "fail_count": len(results) - pass_count,
                "wer": cast(
                    JsonValue, {"median": float(median(wer_values)), "worst": max(wer_values)}
                )
                if wer_values
                else {},
                "cer": cast(
                    JsonValue, {"median": float(median(cer_values)), "worst": max(cer_values)}
                )
                if cer_values
                else {},
                "duration_seconds": cast(JsonValue, _numeric_summary(durations) or {}),
            }
        first = results[0] if results else {}
        planning = cast(dict[str, JsonValue], first.get("planning", {}))
        synthesis = cast(dict[str, JsonValue], first.get("synthesis", {}))
        composition = cast(dict[str, JsonValue], first.get("composition", {}))
        verification = cast(dict[str, JsonValue], first.get("verification", {}))
        result = SelfTestResult(
            schema=E2E_SCHEMA,
            overall_status=cast(Any, overall_status),
            case={"id": case.id, "source_sha256": hashlib.sha256(case.text.encode()).hexdigest()},
            requested=requested,
            resolved=cast(dict[str, JsonValue], first.get("resolved", {})),
            planning=planning,
            synthesis=synthesis,
            composition=composition,
            verification={
                **verification,
                "backend": "redux",
                "model": request.verification.model,
                "device": request.verification.device,
                "backend_package_version": moondream_version(),
                "load_seconds": model_load_seconds,
                "attempts": cast(JsonValue, results),
                **({"short_tail": short_tail_summary} if short_tail_summary is not None else {}),
            },
            timings={
                "total_seconds": elapsed,
                "backend_load_seconds": model_load_seconds,
                "first_attempt": first.get("timings", {}),
            },
            environment={
                "platform": platform.platform(),
                "machine": platform.machine(),
                "python_version": platform.python_version(),
                "cpu_count": os.cpu_count(),
            },
            output=output,
            failure_stage=failure_stage or cast(str | None, first.get("failure_stage")),
            error=cast(Mapping[str, JsonValue] | None, first.get("error")),
            attempts=tuple(cast(Mapping[str, JsonValue], row) for row in results),
        )
        (output / "result.json").write_text(
            json.dumps(
                result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
            )
            + "\n",
            encoding="utf-8",
        )
        _event(self._app, "operation.completed", status=overall_status)
        return result

    def generate_voices(self, request: VoiceMatrixRequest) -> VoiceMatrixResult:
        """Generate one project-backed Redux verification row per runnable voice."""
        from ..integrations.moondream import ReduxSession, moondream_version
        from ..verification.voice_matrix import (
            VOICE_MATRIX_SCHEMA,
            filter_runnable_voices,
            summarize_voice_results,
            write_voice_matrix,
        )

        case = get_case(request.case)
        output = request.output or _default_voice_matrix_path()
        output.mkdir(parents=True, exist_ok=True)
        synthesis = request.synthesis
        query = {
            "language": synthesis.language,
            "engine": synthesis.engine,
            "model": synthesis.model,
        }
        requested = cast(
            dict[str, JsonValue],
            json_value(
                {
                    **query,
                    "engine_options": _safe_options(synthesis.engine_options),
                    "case": case.id,
                    "include_experimental": request.include_experimental,
                    "require_clean_plan": request.require_clean_plan,
                    "thresholds": {
                        "pass_wer": request.pass_wer,
                        "pass_cer": request.pass_cer,
                        "fail_wer": request.fail_wer,
                        "fail_cer": request.fail_cer,
                    },
                }
            ),
        )
        rows: list[dict[str, JsonValue]] = []
        global_error: dict[str, JsonValue] | None = None
        redux_load_seconds: float | None = None
        discovery: dict[str, JsonValue] = {}
        _event(self._app, "operation.started", operation="verification.voice_matrix")
        try:
            listing = self._app.catalog.voices_listing(
                VoiceQuery(
                    language=synthesis.language,
                    engine=synthesis.engine,
                    model=synthesis.model,
                ),
                discovery=request.discovery,
            )
            discovery = cast(dict[str, JsonValue], json_value(listing.discovery))
            voices = filter_runnable_voices(
                listing.items, include_experimental=request.include_experimental
            )
            if not voices:
                global_error = {
                    "code": "verification.no_runnable_voices",
                    "message": "No runnable catalog voices matched the requested engine/model/language.",
                }
            else:
                thresholds = VerificationThresholds(
                    request.pass_wer,
                    request.pass_cer,
                    request.fail_wer,
                    request.fail_cer,
                )
                with ReduxSession(
                    model=request.verification.model,
                    device=request.verification.device,
                ) as redux:
                    redux_load_seconds = redux.load_seconds
                    for ordinal, voice in enumerate(voices, start=1):
                        voice_ref = voice.ref or voice.id
                        voice_dir = (
                            output
                            / f"voice-{ordinal:03d}-{hashlib.sha256(voice_ref.encode()).hexdigest()[:10]}"
                        )
                        voice_dir.mkdir(parents=True, exist_ok=True)
                        voice_request = SelfTestRequest(
                            case=case.id,
                            synthesis=replace(synthesis, voice=voice_ref),
                            planning=request.planning,
                            composition=request.composition,
                            verification=request.verification,
                            require_clean_plan=request.require_clean_plan,
                            output=voice_dir,
                        )
                        attempt = self._run_one(
                            request=voice_request,
                            case=case,
                            ordinal=ordinal,
                            attempt_dir=voice_dir,
                            redux=redux,
                            thresholds=thresholds,
                            operation="verification.voice_matrix",
                        )
                        planning = attempt.get("planning", {})
                        synthesis_result = attempt.get("synthesis", {})
                        composition = attempt.get("composition", {})
                        verification = attempt.get("verification", {})
                        timings = attempt.get("timings", {})
                        planning = planning if isinstance(planning, dict) else {}
                        synthesis_result = (
                            synthesis_result if isinstance(synthesis_result, dict) else {}
                        )
                        composition = composition if isinstance(composition, dict) else {}
                        verification = verification if isinstance(verification, dict) else {}
                        timings = timings if isinstance(timings, dict) else {}
                        planning_status = str(planning.get("status", "fail"))
                        synthesis_status = "pass" if synthesis_result and composition else "not_run"
                        if attempt.get("failure_stage") in {"synthesis", "composition"}:
                            synthesis_status = "fail"
                        row: dict[str, JsonValue] = {
                            "voice": {
                                "ref": voice_ref,
                                "id": voice.id,
                                "engine": voice.engine,
                                "model": voice.model,
                                "language": voice.language,
                                "locale": voice.locale,
                                "gender": voice.gender,
                                "experimental": voice.experimental,
                                "runtime_available": voice.runtime_available,
                            },
                            "status": attempt.get("overall_status", "fail"),
                            "planning_status": "fail"
                            if planning_status == "blocked"
                            else planning_status,
                            "synthesis_status": synthesis_status,
                            "planning": planning,
                            "resolved": attempt.get("resolved", {}),
                            "synthesis": synthesis_result,
                            "composition": composition,
                            "verification": verification,
                            "timings": timings,
                            "source_sha256": attempt.get("source_sha256"),
                            "project_path": attempt.get("project_path"),
                            "error": attempt.get("error"),
                            "failure_stage": attempt.get("failure_stage"),
                        }
                        rows.append(cast(dict[str, JsonValue], json_value(row)))
        except Exception as error:  # noqa: BLE001 - persist catalog/backend failure in the matrix artifact.
            global_error = {
                "code": getattr(error, "code", "verification.voice_matrix_failed"),
                "message": str(error),
                "exception_type": type(error).__name__,
            }
        summary = summarize_voice_results(rows)
        if global_error is not None or summary["failed"] > 0 or not rows:
            overall_status = "fail"
        elif summary["review"] > 0:
            overall_status = "review"
        else:
            overall_status = "pass"
        result = VoiceMatrixResult(
            schema=VOICE_MATRIX_SCHEMA,
            requested=requested,
            overall_status=cast(Any, overall_status),
            query=cast(dict[str, JsonValue], json_value({**query, "discovery": discovery})),
            case={
                "id": case.id,
                "source_sha256": hashlib.sha256(case.text.encode()).hexdigest(),
                "reference_sha256": hashlib.sha256(case.reference_text.encode()).hexdigest(),
            },
            redux={
                "backend": "redux",
                "model": request.verification.model,
                "device": request.verification.device,
                "backend_package_version": moondream_version(),
                "load_seconds": redux_load_seconds,
            },
            summary=cast(dict[str, JsonValue], json_value(summary)),
            results=tuple(cast(Mapping[str, JsonValue], row) for row in rows),
            environment={
                "platform": platform.platform(),
                "machine": platform.machine(),
                "python_version": platform.python_version(),
                "cpu_count": os.cpu_count(),
            },
            output=output,
            error=global_error,
        )
        write_voice_matrix(output, result.to_dict())
        _event(self._app, "operation.completed", status=overall_status)
        return result

    def selftest_timestamps(self, request: TimestampSelfTestRequest) -> TimestampSelfTestResult:
        """Run timestamp QA on canonical merged parent segment audio from a Readio project."""
        from ..errors import ProjectPlanRenderabilityError
        from ..integrations.moondream import ReduxSession, moondream_version
        from ..verification.timestamps import TimestampValidationError, compare_timestamps
        from .types import ProjectPlanInspectionOptions

        case = get_case(request.case)
        output = request.output or _default_timestamp_output_path()
        output.mkdir(parents=True, exist_ok=True)
        project_path = output / "project.readio"
        source = output / "source.txt"
        source.write_text(case.text, encoding="utf-8")
        request_options = request.synthesis
        requested = cast(
            dict[str, JsonValue],
            json_value(
                {
                    "case": case.id,
                    "synthesis": {
                        "language": request_options.language,
                        "engine": request_options.engine,
                        "model": request_options.model,
                        "voice": request_options.voice,
                        "engine_options": _safe_options(request_options.engine_options),
                    },
                    "require_clean_plan": request.require_clean_plan,
                }
            ),
        )
        _event(self._app, "operation.started", operation="verification.timestamps")
        start_all = perf_counter()
        failure_stage: str | None = None
        error_payload: dict[str, JsonValue] | None = None
        project = None
        planning: dict[str, JsonValue] = {}
        resolved: dict[str, JsonValue] = {}
        synthesis: dict[str, JsonValue] = {}
        rows: list[dict[str, Any]] = []
        model_load_seconds: float | None = None
        try:
            failure_stage = "project_creation"
            project = self._app.projects.create(source, output=project_path)
            failure_stage = "resolution"
            resolution = self._app.projects.resolve_synthesis(
                project, request.synthesis, use_saved_settings=False
            )
            resolved = cast(
                dict[str, JsonValue],
                json_value(
                    {
                        "language": resolution.language,
                        "engine": resolution.engine,
                        "model": resolution.model,
                        "voice": resolution.voice,
                        "voice_ref": request.synthesis.voice,
                    }
                ),
            )
            failure_stage = "planning"
            _event(self._app, "stage.started", "plan", operation="verification.timestamps")
            try:
                plan = self._app.projects.plan(project, options=request.planning)
            except ProjectPlanRenderabilityError as blocked:
                inspection = self._app.projects.inspect_plan(
                    project,
                    options=ProjectPlanInspectionOptions(
                        attempt="latest", issues=True, repairs=True, source_context=1
                    ),
                )
                attempt = inspection.attempt
                issues = inspection.issues
                planning = {
                    "status": "blocked",
                    "attempt_id": blocked.attempt_id or (attempt.attempt_id if attempt else None),
                    "attempt_status": blocked.attempt_status
                    or (attempt.status if attempt else "blocked"),
                    "issues": len(issues) if issues else len(blocked.issues),
                    "repairs_available": len(inspection.repairs),
                    "unsafe_issues": sum(issue.repair_safe is False for issue in issues),
                    "issue_summary": [_bounded_issue(issue) for issue in issues[:20]],
                }
                error_payload = {
                    "code": blocked.code,
                    "message": str(blocked),
                    "attempt_id": planning["attempt_id"],
                }
                _event(
                    self._app,
                    "stage.completed",
                    "plan",
                    operation="verification.timestamps",
                    status="fail",
                    attempt_id=planning["attempt_id"],
                )
                failure_stage = "planning"
            else:
                planning = {
                    "status": "repaired" if plan.repairs else "pass",
                    "attempt_id": plan.attempt_id,
                    "renderability_mode": plan.renderability_mode,
                    "renderability_guaranteed": plan.renderability_guaranteed,
                    "repairs": plan.repairs,
                    "scope_count": len(plan.scopes),
                    "planned_units": sum(scope.units or 0 for scope in plan.scopes),
                    "reused_scopes": list(plan.reused_scopes),
                    "rebuilt_scopes": list(plan.rebuilt_scopes),
                    "diagnostics": [item.to_dict() for item in plan.diagnostics],
                }
                _event(
                    self._app,
                    "stage.completed",
                    "plan",
                    operation="verification.timestamps",
                    attempt_id=plan.attempt_id,
                )
                if not plan.scopes:
                    raise RuntimeError("planning produced no scopes")
                failure_stage = "synthesis"
                _event(self._app, "stage.started", "synthesis", operation="verification.timestamps")
                synthesis_started = perf_counter()
                synthesized = self._app.projects.synthesize(
                    project, request.synthesis, activate=True
                )
                synthesis = {
                    "profile_id": synthesized.profile_id,
                    "rendered_units": synthesized.rendered,
                    "reused_units": synthesized.reused,
                    "selected_units": synthesized.selected_units,
                    "artifacts": len(synthesized.artifacts),
                    "seconds": max(0.0, perf_counter() - synthesis_started),
                }
                _event(
                    self._app, "stage.completed", "synthesis", operation="verification.timestamps"
                )
                if not synthesized.artifacts:
                    raise RuntimeError("synthesis produced no canonical segment artifacts")
                failure_stage = "redux_load"
                with ReduxSession(
                    model=request.verification.model,
                    device=request.verification.device,
                ) as redux:
                    model_load_seconds = redux.load_seconds
                    _event(
                        self._app,
                        "stage.started",
                        "verification",
                        operation="verification.timestamps",
                    )
                    for artifact in synthesized.artifacts:
                        segment_started = perf_counter()
                        segment_error: dict[str, JsonValue] | None = None
                        transcript_result = None
                        try:
                            transcript_result = redux.transcribe(
                                artifact.audio_path, timestamps="word"
                            )
                            verification = _verify_text(
                                expected=artifact.text,
                                transcript=transcript_result.text,
                            )
                            timestamp_report = compare_timestamps(
                                text=artifact.text,
                                transcription=transcript_result,
                                sample_rate=artifact.sample_rate,
                                frames=artifact.frames,
                                native_timings=artifact.word_timings,
                                context={
                                    "scope_id": artifact.scope_id,
                                    "segment_id": artifact.segment_id,
                                },
                            )
                            status = verification.status
                            if request.require_clean_plan and plan.repairs and status == "pass":
                                status = "review"
                            row = {
                                "scope_id": artifact.scope_id,
                                "segment_id": artifact.segment_id,
                                "text_sha256": hashlib.sha256(artifact.text.encode()).hexdigest(),
                                "audio": str(artifact.audio_path),
                                "sample_rate": artifact.sample_rate,
                                "frames": artifact.frames,
                                "audio_sha256": artifact.audio_sha256,
                                "speech_hash": artifact.speech_hash,
                                "synthesis_key": artifact.synthesis_key,
                                "profile_id": artifact.profile_id,
                                "lowering_sha256": artifact.lowering_sha256,
                                "transcript": transcript_result.text,
                                "transcript_status": verification.status,
                                "wer": verification.wer,
                                "cer": verification.cer,
                                "status": status,
                                **timestamp_report,
                            }
                        except TimestampValidationError as timestamp_error:
                            segment_error = {
                                "code": "verification.invalid_timestamps",
                                "message": str(timestamp_error),
                            }
                            row = {
                                "scope_id": artifact.scope_id,
                                "segment_id": artifact.segment_id,
                                "audio": str(artifact.audio_path),
                                "audio_sha256": artifact.audio_sha256,
                                "status": "fail",
                                "timing_structure": {"status": "fail"},
                                "error": segment_error,
                            }
                        rows.append(
                            cast(
                                dict[str, JsonValue],
                                json_value(
                                    {
                                        **row,
                                        "seconds": max(0.0, perf_counter() - segment_started),
                                    }
                                ),
                            )
                        )
                        _event(
                            self._app,
                            "stage.completed",
                            "verification",
                            operation="verification.timestamps",
                            status=str(row.get("status", "fail")),
                            scope_id=artifact.scope_id,
                            segment_id=artifact.segment_id,
                        )
                    failure_stage = None
        except Exception as caught:  # noqa: BLE001 - preserve pipeline failures in the self-test report.
            if error_payload is None:
                error_payload = {
                    "code": getattr(caught, "code", "verification.timestamp_selftest_failed"),
                    "message": str(caught),
                    "exception_type": type(caught).__name__,
                }
        transcript_statuses = [str(row.get("transcript_status", "fail")) for row in rows]
        if (
            error_payload is not None
            or any(row.get("status") == "fail" for row in rows)
            or not rows
        ):
            overall_status = "fail"
        elif "review" in transcript_statuses or any(row.get("status") == "review" for row in rows):
            overall_status = "review"
        else:
            overall_status = "pass"
        expected_total = sum(
            int(row.get("alignment", {}).get("expected_words", 0))
            for row in rows
            if isinstance(row.get("alignment"), dict)
        )
        matched_total = sum(
            int(row.get("alignment", {}).get("matched_words", 0))
            for row in rows
            if isinstance(row.get("alignment"), dict)
        )
        summary = {
            "segments": len(rows),
            "passed": sum(row.get("status") == "pass" for row in rows),
            "review": sum(row.get("status") == "review" for row in rows),
            "failed": sum(row.get("status") == "fail" for row in rows),
            "expected_words": expected_total,
            "matched_words": matched_total,
            "alignment_coverage": matched_total / expected_total if expected_total else 0.0,
            "native_comparison_segments": sum(
                row.get("native_comparison", {}).get("status") == "available"
                for row in rows
                if isinstance(row.get("native_comparison"), dict)
            ),
        }
        result = TimestampSelfTestResult(
            schema=TIMESTAMP_SELFTEST_SCHEMA,
            overall_status=cast(Any, overall_status),
            case={"id": case.id, "source_sha256": hashlib.sha256(case.text.encode()).hexdigest()},
            requested=requested,
            resolved=resolved,
            planning=planning,
            synthesis=synthesis,
            redux={
                "backend": "redux",
                "model": request.verification.model,
                "device": request.verification.device,
                "backend_package_version": moondream_version(),
                "load_seconds": model_load_seconds,
            },
            summary=cast(dict[str, JsonValue], json_value(summary)),
            results=tuple(cast(Mapping[str, JsonValue], row) for row in rows),
            timings={
                "total_seconds": max(0.0, perf_counter() - start_all),
                "synthesis_seconds": synthesis.get("seconds", 0.0),
                "backend_load_seconds": model_load_seconds,
            },
            environment={
                "platform": platform.platform(),
                "machine": platform.machine(),
                "python_version": platform.python_version(),
                "cpu_count": os.cpu_count(),
            },
            output=output,
            failure_stage=failure_stage,
            error=error_payload,
        )
        (output / "result.json").write_text(
            json.dumps(
                result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
            )
            + "\n",
            encoding="utf-8",
        )
        _event(
            self._app,
            "operation.completed",
            operation="verification.timestamps",
            status=overall_status,
        )
        return result

    def generate_timestamps(self, request: TimestampGenerationRequest) -> TimestampGenerationResult:
        """Explicitly derive and cache segment-local ASR timings without changing TTS artifacts."""
        from ..engines.base import validate_word_timings
        from ..integrations.moondream import ReduxSession, moondream_version
        from ..project import atomic_write_json, canonical_json
        from ..verification.timestamps import (
            ALIGNMENT_SCHEMA,
            TIMESTAMP_ARTIFACT_FORMAT,
            TIMESTAMP_ARTIFACT_SCHEMA_VERSION,
            TimestampValidationError,
            compare_timestamps,
            timings_from_mappings,
        )

        _event(self._app, "operation.started", operation="verification.timestamps.generate")
        started = perf_counter()
        resolution = self._app.projects.resolve_synthesis(request.project, request.synthesis)
        plan = self._app.projects.plan(request.project, options=request.planning)
        if not plan.scopes:
            raise IntegrationError(
                "Timestamp generation requires a renderable project plan.",
                code="verification.timestamp_plan_empty",
            )
        synthesis_started = perf_counter()
        synthesized = self._app.projects.synthesize(
            request.project, request.synthesis, activate=True
        )
        synthesis_seconds = max(0.0, perf_counter() - synthesis_started)
        if not synthesized.artifacts:
            raise IntegrationError(
                "Timestamp generation found no canonical segment synthesis artifacts.",
                code="verification.timestamp_artifacts_missing",
            )
        project_ref = synthesized.project
        cache_root = project_ref.root / ".readio" / "verification" / "timestamps" / "derived"
        cache_root.mkdir(parents=True, exist_ok=True)
        backend_version = moondream_version()
        requested = cast(
            dict[str, JsonValue],
            json_value(
                {
                    "project": str(project_ref.root),
                    "synthesis": {
                        "language": request.synthesis.language,
                        "engine": request.synthesis.engine,
                        "model": request.synthesis.model,
                        "voice": request.synthesis.voice,
                        "engine_options": _safe_options(request.synthesis.engine_options),
                    },
                    "verification": {
                        "backend": "redux",
                        "model": request.verification.model,
                        "device": request.verification.device,
                    },
                    "force_refresh": request.force_refresh,
                }
            ),
        )
        resolved = cast(
            dict[str, JsonValue],
            json_value(
                {
                    "language": resolution.language,
                    "engine": resolution.engine,
                    "model": resolution.model,
                    "voice": resolution.voice,
                    "profile_id": synthesized.profile_id,
                }
            ),
        )
        synthesis_summary = {
            "profile_id": synthesized.profile_id,
            "rendered_units": synthesized.rendered,
            "reused_units": synthesized.reused,
            "selected_units": synthesized.selected_units,
            "artifacts": len(synthesized.artifacts),
            "seconds": synthesis_seconds,
            "plan_attempt_id": plan.attempt_id,
            "plan_repairs": plan.repairs,
        }
        pending: list[tuple[Any, str, Path, dict[str, JsonValue], str]] = []
        rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for artifact in synthesized.artifacts:
            identity = cast(
                dict[str, JsonValue],
                json_value(
                    {
                        "source": "asr",
                        "backend": "redux",
                        "model": request.verification.model,
                        "device": request.verification.device,
                        "backend_package_version": backend_version,
                        "alignment_schema": ALIGNMENT_SCHEMA,
                        "scope_id": artifact.scope_id,
                        "segment_id": artifact.segment_id,
                        "speech_hash": artifact.speech_hash,
                        "synthesis_key": artifact.synthesis_key,
                        "profile_id": artifact.profile_id,
                        "lowering_sha256": artifact.lowering_sha256,
                        "audio_sha256": artifact.audio_sha256,
                        "text_sha256": hashlib.sha256(artifact.text.encode("utf-8")).hexdigest(),
                        "sample_rate": artifact.sample_rate,
                        "frames": artifact.frames,
                    }
                ),
            )
            cache_key = hashlib.sha256(canonical_json(identity)).hexdigest()
            cache_path = cache_root / f"{cache_key}.json"
            if not request.force_refresh and cache_path.is_file():
                try:
                    cached = json.loads(cache_path.read_text(encoding="utf-8"))
                    raw_timings = cached.get("word_timings") if isinstance(cached, dict) else None
                    if (
                        isinstance(cached, dict)
                        and cached.get("format") == TIMESTAMP_ARTIFACT_FORMAT
                        and cached.get("schema_version") == TIMESTAMP_ARTIFACT_SCHEMA_VERSION
                        and cached.get("cache_key") == cache_key
                        and cached.get("identity") == identity
                        and isinstance(cached.get("alignment"), dict)
                        and isinstance(cached.get("timing_structure"), dict)
                        and isinstance(cached.get("operations"), list)
                        and cached.get("artifact_sha256")
                        == hashlib.sha256(
                            canonical_json(
                                {
                                    key: value
                                    for key, value in cached.items()
                                    if key != "artifact_sha256"
                                }
                            )
                        ).hexdigest()
                        and isinstance(raw_timings, list)
                        and all(isinstance(item, Mapping) for item in raw_timings)
                    ):
                        valid_timings = validate_word_timings(
                            text=artifact.text,
                            frame_count=artifact.frames,
                            timings=timings_from_mappings(raw_timings),
                            context={
                                "scope_id": artifact.scope_id,
                                "segment_id": artifact.segment_id,
                            },
                        )
                        rows_by_key[(artifact.scope_id, artifact.segment_id)] = {
                            "scope_id": artifact.scope_id,
                            "segment_id": artifact.segment_id,
                            "cache_key": cache_key,
                            "cache_path": str(cache_path),
                            "cache_status": "hit",
                            "alignment": cached.get("alignment", {}),
                            "timing_structure": cached.get("timing_structure", {}),
                            "word_timings": [
                                {
                                    "text": timing.text,
                                    "char_start": timing.char_start,
                                    "char_end": timing.char_end,
                                    "start_sample": timing.start_sample,
                                    "end_sample": timing.end_sample,
                                }
                                for timing in valid_timings
                            ],
                            "status": "pass",
                        }
                        continue
                except (OSError, ValueError, TypeError, TimestampValidationError):
                    pass
            pending.append(
                (
                    artifact,
                    cache_key,
                    cache_path,
                    identity,
                    "refreshed" if request.force_refresh else "created",
                )
            )

        model_load_seconds: float | None = None
        transcription_seconds = 0.0
        if pending:
            _event(
                self._app,
                "stage.started",
                "verification",
                operation="verification.timestamps.generate",
            )
            with ReduxSession(
                model=request.verification.model,
                device=request.verification.device,
            ) as redux:
                model_load_seconds = redux.load_seconds
                for artifact, cache_key, cache_path, identity, cache_status in pending:
                    segment_started = perf_counter()
                    try:
                        transcription = redux.transcribe(artifact.audio_path, timestamps="word")
                        report = compare_timestamps(
                            text=artifact.text,
                            transcription=transcription,
                            sample_rate=artifact.sample_rate,
                            frames=artifact.frames,
                            context={
                                "scope_id": artifact.scope_id,
                                "segment_id": artifact.segment_id,
                            },
                        )
                        record = {
                            "format": TIMESTAMP_ARTIFACT_FORMAT,
                            "schema_version": TIMESTAMP_ARTIFACT_SCHEMA_VERSION,
                            **identity,
                            "identity": identity,
                            "cache_key": cache_key,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "alignment": report["alignment"],
                            "timing_structure": report["timing_structure"],
                            "operations": report["operations"],
                            "word_timings": report["derived_word_timings"],
                        }
                        record["artifact_sha256"] = hashlib.sha256(
                            canonical_json(record)
                        ).hexdigest()
                        atomic_write_json(cache_path, record)
                        transcription_seconds += max(0.0, perf_counter() - segment_started)
                        rows_by_key[(artifact.scope_id, artifact.segment_id)] = {
                            "scope_id": artifact.scope_id,
                            "segment_id": artifact.segment_id,
                            "cache_key": cache_key,
                            "cache_path": str(cache_path),
                            "cache_status": cache_status,
                            "alignment": report["alignment"],
                            "timing_structure": report["timing_structure"],
                            "word_timings": report["derived_word_timings"],
                            "status": "pass",
                        }
                    except Exception as error:  # noqa: BLE001 - isolate segment analysis failures.
                        rows_by_key[(artifact.scope_id, artifact.segment_id)] = {
                            "scope_id": artifact.scope_id,
                            "segment_id": artifact.segment_id,
                            "cache_key": cache_key,
                            "cache_path": str(cache_path),
                            "cache_status": cache_status,
                            "status": "fail",
                            "error": {
                                "code": getattr(
                                    error, "code", "verification.timestamp_generation_failed"
                                ),
                                "message": str(error),
                                "exception_type": type(error).__name__,
                            },
                        }
            _event(
                self._app,
                "stage.completed",
                "verification",
                operation="verification.timestamps.generate",
                status="fail"
                if any(row.get("status") == "fail" for row in rows_by_key.values())
                else "pass",
            )
        rows = [
            rows_by_key[(artifact.scope_id, artifact.segment_id)]
            for artifact in synthesized.artifacts
        ]
        failed = sum(row.get("status") == "fail" for row in rows)
        successful = len(rows) - failed
        overall_status = "pass" if not failed and rows else ("review" if successful else "fail")
        expected_words = sum(
            int(row.get("alignment", {}).get("expected_words", 0))
            for row in rows
            if isinstance(row.get("alignment"), dict)
        )
        matched_words = sum(
            int(row.get("alignment", {}).get("matched_words", 0))
            for row in rows
            if isinstance(row.get("alignment"), dict)
        )
        summary = {
            "segments": len(rows),
            "generated": sum(
                row.get("cache_status") in {"created", "refreshed"} and row.get("status") == "pass"
                for row in rows
            ),
            "cached": sum(row.get("cache_status") == "hit" for row in rows),
            "failed": failed,
            "expected_words": expected_words,
            "matched_words": matched_words,
            "alignment_coverage": matched_words / expected_words if expected_words else 0.0,
        }
        result = TimestampGenerationResult(
            schema=TIMESTAMP_GENERATION_SCHEMA,
            overall_status=cast(Any, overall_status),
            project=project_ref,
            requested=requested,
            resolved=resolved,
            synthesis=cast(dict[str, JsonValue], json_value(synthesis_summary)),
            redux={
                "backend": "redux",
                "model": request.verification.model,
                "device": request.verification.device,
                "backend_package_version": backend_version,
                "load_seconds": model_load_seconds,
            },
            summary=cast(dict[str, JsonValue], json_value(summary)),
            results=tuple(cast(Mapping[str, JsonValue], json_value(row)) for row in rows),
            timings={
                "total_seconds": max(0.0, perf_counter() - started),
                "synthesis_seconds": synthesis_seconds,
                "backend_load_seconds": model_load_seconds,
                "transcription_seconds": transcription_seconds,
            },
            cache_root=cache_root,
            output=request.output,
            error=None,
        )
        if request.output is not None:
            request.output.mkdir(parents=True, exist_ok=True)
            (request.output / "result.json").write_text(
                json.dumps(
                    result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
                )
                + "\n",
                encoding="utf-8",
            )
        _event(
            self._app,
            "operation.completed",
            operation="verification.timestamps.generate",
            status=overall_status,
        )
        return result

    def _run_one(
        self,
        *,
        request: SelfTestRequest,
        case: VerificationCase,
        ordinal: int,
        attempt_dir: Path,
        redux: Any,
        thresholds: VerificationThresholds | None = None,
        operation: str = "verification.e2e",
    ) -> dict[str, JsonValue]:
        from ..errors import ProjectPlanRenderabilityError
        from .types import ProjectPlanInspectionOptions

        started = perf_counter()
        timings: dict[str, JsonValue] = {}
        thresholds = thresholds or VerificationThresholds()
        case_text = case.text
        reference_text = case.reference_text
        source = attempt_dir / "source.txt"
        source.write_text(case_text, encoding="utf-8")
        project_path = attempt_dir / "project.readio"
        values: dict[str, JsonValue] = {
            "ordinal": ordinal,
            "project_path": str(project_path),
            "timings": timings,
            "source_sha256": _sha256(source),
        }
        stage = "project_creation"
        try:
            project_started = perf_counter()
            project = self._app.projects.create(source, output=project_path)
            timings["project_seconds"] = max(0.0, perf_counter() - project_started)
            stage = "resolution"
            resolution_started = perf_counter()
            resolution = self._app.projects.resolve_synthesis(
                project, request.synthesis, use_saved_settings=False
            )
            timings["resolution_seconds"] = max(0.0, perf_counter() - resolution_started)
            resolved: dict[str, JsonValue] = cast(
                dict[str, JsonValue],
                json_value(
                    {
                        "language": resolution.language,
                        "engine": resolution.engine,
                        "model": resolution.model,
                        "voice": resolution.voice,
                        "voice_ref": request.synthesis.voice,
                    }
                ),
            )
            values["resolved"] = resolved
            stage = "planning"
            _event(self._app, "stage.started", "plan", operation=operation)
            planning_started = perf_counter()
            try:
                plan = self._app.projects.plan(project, options=request.planning)
            except ProjectPlanRenderabilityError as error:
                timings["planning_seconds"] = max(0.0, perf_counter() - planning_started)
                inspection = self._app.projects.inspect_plan(
                    project,
                    options=ProjectPlanInspectionOptions(
                        attempt="latest", issues=True, repairs=True, source_context=1
                    ),
                )
                attempt_info = inspection.attempt
                issues = inspection.issues
                repairs = inspection.repairs
                plan_info: dict[str, JsonValue] = {
                    "status": "blocked",
                    "seconds": timings.get("planning_seconds"),
                    "attempt_id": error.attempt_id
                    or (attempt_info.attempt_id if attempt_info else None),
                    "attempt_status": error.attempt_status
                    or (attempt_info.status if attempt_info else "blocked"),
                    "issues": len(issues) if issues else len(error.issues),
                    "repairs_available": len(repairs),
                    "unsafe_issues": sum(issue.repair_safe is False for issue in issues),
                    "issue_summary": [_bounded_issue(issue) for issue in issues[:20]],
                }
                _event(
                    self._app,
                    "stage.completed",
                    "plan",
                    operation=operation,
                    attempt_id=plan_info["attempt_id"],
                )
                values.update(
                    {
                        "overall_status": "fail",
                        "failure_stage": "planning",
                        "planning": plan_info,
                        "error": {
                            "code": error.code,
                            "message": str(error),
                            "attempt_id": plan_info["attempt_id"],
                            "attempt_status": plan_info["attempt_status"],
                        },
                    }
                )
                return cast(dict[str, JsonValue], json_value(values))
            timings["planning_seconds"] = max(0.0, perf_counter() - planning_started)
            planning = {
                "status": "repaired" if plan.repairs else "pass",
                "seconds": max(0.0, perf_counter() - planning_started),
                "attempt_id": plan.attempt_id,
                "renderability_mode": plan.renderability_mode,
                "renderability_guaranteed": plan.renderability_guaranteed,
                "activated": plan.activated,
                "repairs": plan.repairs,
                "reused_scopes": list(plan.reused_scopes),
                "rebuilt_scopes": list(plan.rebuilt_scopes),
                "diagnostics": [diagnostic.to_dict() for diagnostic in plan.diagnostics],
                "scope_count": len(plan.scopes),
                "planned_units": sum(scope.units or 0 for scope in plan.scopes),
            }
            values["planning"] = cast(dict[str, JsonValue], json_value(planning))
            if not plan.scopes:
                raise RuntimeError("planning produced no scopes")
            _event(
                self._app,
                "stage.completed",
                "plan",
                operation=operation,
                attempt_id=plan.attempt_id,
            )
            stage = "synthesis"
            _event(self._app, "stage.started", "synthesis", operation=operation)
            synthesis_started = perf_counter()
            synthesized = self._app.projects.synthesize(project, request.synthesis, activate=True)
            timings["synthesis_seconds"] = max(0.0, perf_counter() - synthesis_started)
            synthesis = {
                "profile_id": synthesized.profile_id,
                "selected_units": synthesized.selected_units,
                "rendered_units": synthesized.rendered,
                "reused_units": synthesized.reused,
                "activated": synthesized.activated,
            }
            values["synthesis"] = cast(dict[str, JsonValue], json_value(synthesis))
            _event(self._app, "stage.completed", "synthesis", operation=operation)
            stage = "composition"
            _event(self._app, "stage.started", "composition", operation=operation)
            composition_started = perf_counter()
            composed = self._app.projects.compose(project, request.composition)
            timings["composition_seconds"] = max(0.0, perf_counter() - composition_started)
            master = composed.master_path
            if master is None or not master.is_file() or composed.frames <= 0:
                raise RuntimeError("composition did not produce a valid master WAV")

            info = probe_audio(master)
            composition = {
                "composition_id": composed.composition_id,
                "items": composed.items,
                "sample_rate": info.sample_rate,
                "channels": info.channels,
                "frames": info.frames,
                "audio_seconds": info.frames / info.sample_rate,
                "wav": str(master),
                "wav_sha256": _sha256(master),
                "loudness": composed.loudness.to_dict() if composed.loudness else None,
            }
            values["composition"] = cast(dict[str, JsonValue], json_value(composition))
            _event(self._app, "stage.completed", "composition", operation=operation)
            stage = "verification"
            _event(self._app, "stage.started", "verification", operation=operation)
            redux_started = perf_counter()
            transcription = redux.transcribe(master, timestamps="word")
            timings["redux_seconds"] = max(0.0, perf_counter() - redux_started)
            verified = _verify_text(
                expected=reference_text, transcript=transcription.text, thresholds=thresholds
            )
            terminal_complete = normalize_text(transcription.text) == normalize_text(reference_text)
            verification = {
                "status": verified.status,
                "wer": verified.wer,
                "cer": verified.cer,
                "transcript": transcription.text,
                "terminal_complete": terminal_complete
                if case.id == "pocket-short-tail-v1"
                else None,
            }
            values["verification"] = cast(dict[str, JsonValue], json_value(verification))
            status = verified.status
            if case.id == "pocket-short-tail-v1" and not terminal_complete:
                status = "fail"
            if request.require_clean_plan and plan.repairs and status == "pass":
                status = "review"
            values["overall_status"] = status
            _event(self._app, "stage.completed", "verification", operation=operation, status=status)
            values["seconds"] = max(0.0, perf_counter() - started)
            return cast(dict[str, JsonValue], json_value(values))
        except Exception as error:  # noqa: BLE001 - convert stage failures into per-attempt evidence.
            values.update(
                {
                    "overall_status": "fail",
                    "failure_stage": stage,
                    "error": {
                        "code": getattr(error, "code", "verification.pipeline_error"),
                        "message": str(error),
                        "attempt_id": getattr(error, "attempt_id", None),
                    },
                    "seconds": max(0.0, perf_counter() - started),
                }
            )
            return cast(dict[str, JsonValue], json_value(values))


def _default_output_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return Path("verification-output") / f"e2e-{stamp}"


def _default_timestamp_output_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return Path("verification-output") / f"timestamps-{stamp}"


def _default_voice_matrix_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return Path("verification-output") / f"voices-{stamp}"


__all__ = ["E2E_SCHEMA", "VerificationService"]
