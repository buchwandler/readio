"""One-shot and live speech operations exposed through :mod:`readio.api`."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ..audio import RenderProgress
from ..engines.registry import get_engine
from ..errors import ManifestError, ReadioError
from ..execution import BoundedRenderResult, ResolvedExecutionV2, execute_bounded_v2
from ..formats import (
    AudioFormat,
    ensure_audio_format_available,
    normalize_audio_output_path,
    resolve_audio_format,
)
from ..manifest import (
    RENDER_MANIFEST_SCHEMA_V2,
    build_render_manifest_v2,
    manifest_path_for,
    write_render_manifest,
)
from ..paths import resolve_render_output
from ..plan import resolve_execution_v2, resolve_plan_v2
from ..reader import render_live as render_live_internal
from ..synthesis import resolve_synthesis_request
from ..wave import atomic_audio_path, create_audio_sink
from .errors import (
    ExecutionError,
    InvalidRequestError,
    OutputError,
    PlannedOutputError,
    PlanNotExecutableError,
    ResolutionError,
    translate_exception,
)
from .events import EventHandler, EventStage, ReadioEvent, compose_event_handlers
from .types import (
    Diagnostic,
    OutputRequest,
    PlanRequest,
    RenderResult,
    ResolvedPlan,
    SynthesisRequest,
)

if TYPE_CHECKING:
    from ..synthesis import ResolvedSynthesis
    from .app import Readio
    from .types import AudioSink


class SpeechService:
    """Plan and execute bounded or live Readio speech operations."""

    def __init__(self, app: Readio) -> None:
        self._app = app

    def plan(self, request: PlanRequest) -> ResolvedPlan:
        """Resolve a bounded request without loading a TTS model or session."""
        try:
            return resolve_plan_v2(self._app.config, request)
        except ReadioError:
            raise
        except Exception as error:
            raise translate_exception(
                error,
                error_type=ResolutionError,
                code="speech.plan_failed",
            ) from error

    def render(
        self,
        request: PlanRequest,
        *,
        on_event: EventHandler | None = None,
        write_manifest: bool = False,
    ) -> RenderResult:
        if request.output.mode == "playback":
            if write_manifest:
                raise InvalidRequestError(
                    "a render manifest requires file output",
                    code="request.manifest_requires_file",
                )
            return self._play(request, on_event=on_event)

        resolved = self._resolve_execution(request)
        plan = resolved.plan
        output = plan.output.path
        audio_format = plan.output.format
        if output is None or audio_format is None:
            raise InvalidRequestError(
                "the resolved request has no file output destination",
                code="request.output_required",
            )

        handler = self._handler(on_event)
        self._notify(handler, ReadioEvent(kind="operation.started", operation="render"))
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            with (
                atomic_audio_path(output, force=plan.output.force) as temporary,
                create_audio_sink(temporary, cast(AudioFormat, audio_format)) as sink,
            ):
                execution_result = self._execute(
                    resolved,
                    sink,
                    operation="render",
                    handler=handler,
                )
            manifest_path = None
            if write_manifest:
                manifest_path = manifest_path_for(output)
                payload = build_render_manifest_v2(
                    plan_v2=plan,
                    summary=execution_result.summary,
                    output=output,
                    composition=execution_result.composition,
                )
                try:
                    write_render_manifest(manifest_path, payload)
                except OSError as error:
                    raise ManifestError(
                        f"failed to write render manifest: {error}",
                        audio_path=output,
                        manifest_path=manifest_path,
                    ) from error
        except ReadioError:
            raise
        except Exception as error:
            error_type = (
                OutputError if isinstance(error, (FileExistsError, OSError)) else ExecutionError
            )
            if isinstance(error, FileExistsError):
                code = "output.exists"
            elif isinstance(error, OSError):
                code = "output.write_failed"
            else:
                code = "speech.render_failed"
            raise translate_exception(error, error_type=error_type, code=code) from error

        self._notify(handler, ReadioEvent(kind="operation.completed", operation="render"))
        return self._result(plan, execution_result, output_path=output, manifest_path=manifest_path)

    def render_to_sink(
        self,
        request: PlanRequest,
        sink: AudioSink,
        *,
        on_event: EventHandler | None = None,
    ) -> RenderResult:
        """Render to a caller-owned sink without closing it."""
        request = self._sink_request(request)
        resolved = self._resolve_execution(request)
        handler = self._handler(on_event)
        self._notify(handler, ReadioEvent(kind="operation.started", operation="render"))
        execution_result = self._execute(resolved, sink, operation="render", handler=handler)
        self._notify(handler, ReadioEvent(kind="operation.completed", operation="render"))
        return self._result(resolved.plan, execution_result)

    def speak(
        self,
        request: PlanRequest,
        *,
        on_event: EventHandler | None = None,
    ) -> RenderResult:
        """Play a bounded request using a Readio-owned playback sink."""
        request = replace(
            request,
            operation="speak",
            output=replace(
                request.output,
                mode="playback",
                requested_format=None,
                requested_path=None,
                force=False,
                bitrate=None,
            ),
        )
        return self._play(request, on_event=on_event)

    def render_live(
        self,
        lines: Iterable[str],
        sink: AudioSink,
        *,
        synthesis: SynthesisRequest | None = None,
        unit: str | None = None,
        on_event: EventHandler | None = None,
    ) -> RenderResult:
        """Consume live text without taking ownership of its iterable or sink."""
        resolved_synthesis = self._resolve_live_synthesis(synthesis or SynthesisRequest())
        self._validate_live_capability(resolved_synthesis.engine)
        return self._render_live_resolved(
            lines,
            sink,
            resolved_synthesis=resolved_synthesis,
            unit=unit,
            on_event=on_event,
        )

    def render_live_to_file(
        self,
        lines: Iterable[str],
        output: OutputRequest,
        *,
        synthesis: SynthesisRequest | None = None,
        unit: str | None = None,
        on_event: EventHandler | None = None,
    ) -> RenderResult:
        """Render live text to a Readio-owned file and sink."""
        if output.mode != "file":
            raise InvalidRequestError(
                "render_live_to_file requires file output",
                code="request.file_output_required",
            )

        resolved_synthesis = self._resolve_live_synthesis(synthesis or SynthesisRequest())
        self._validate_live_capability(resolved_synthesis.engine)

        try:
            audio_format = resolve_audio_format(
                requested=output.requested_format,
                output=output.requested_path,
            )
            output_path = resolve_render_output(
                self._app.config,
                explicit=output.requested_path,
                input_path=None,
                audio_format=audio_format,
            )
            output_path = normalize_audio_output_path(output_path, audio_format)
        except ValueError as error:
            raise InvalidRequestError(
                str(error),
                code="request.output_format_invalid",
            ) from error

        try:
            ensure_audio_format_available(audio_format)
        except Exception as error:
            raise OutputError(
                str(error),
                code="output.encoder_unavailable",
            ) from error

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with (
                atomic_audio_path(output_path, force=output.force) as temporary,
                create_audio_sink(temporary, audio_format) as sink,
            ):
                result = self._render_live_resolved(
                    lines,
                    sink,
                    resolved_synthesis=resolved_synthesis,
                    unit=unit,
                    on_event=on_event,
                )
        except (InvalidRequestError, ResolutionError, ExecutionError):
            raise
        except FileExistsError as error:
            raise OutputError(str(error), code="output.exists") from error
        except OSError as error:
            raise OutputError(str(error), code="output.write_failed") from error
        except ReadioError as error:
            raise ExecutionError(
                str(error),
                details={"exception_type": type(error).__name__},
                code="speech.live_render_failed",
            ) from error
        except Exception as error:
            raise OutputError(
                str(error),
                code="output.write_failed",
            ) from error

        return replace(result, output_path=output_path, audio_format=audio_format)

    def _resolve_live_synthesis(self, synthesis: SynthesisRequest) -> ResolvedSynthesis:
        try:
            return resolve_synthesis_request(self._app.config, synthesis)
        except ReadioError:
            raise
        except Exception as error:
            raise translate_exception(
                error,
                error_type=ResolutionError,
                code="speech.live_resolution_failed",
            ) from error

    def _validate_live_capability(self, engine: str | None) -> None:
        engine = engine or self._app.config.reader.engine
        try:
            adapter = get_engine(engine)
        except ValueError as error:
            raise ResolutionError(
                str(error),
                code="speech.engine_unavailable",
            ) from error
        if not adapter.capabilities().supports_live:
            raise InvalidRequestError(
                f"Live streaming is not supported by engine {engine!r}.",
                code="speech.live_unsupported",
            )

    def _render_live_resolved(
        self,
        lines: Iterable[str],
        sink: AudioSink,
        *,
        resolved_synthesis: ResolvedSynthesis,
        unit: str | None,
        on_event: EventHandler | None,
    ) -> RenderResult:
        handler = self._handler(on_event)
        self._notify(handler, ReadioEvent(kind="operation.started", operation="render_live"))

        def progress(event: RenderProgress) -> None:
            self._notify(
                handler,
                ReadioEvent(
                    kind="progress",
                    operation="render_live",
                    stage="synthesis",
                    progress_kind="unit.completed",
                    total=event.total_units,
                    sample_count=event.sample_count,
                    sample_rate=event.sample_rate,
                    audio_seconds=(
                        event.sample_count / event.sample_rate if event.sample_rate else None
                    ),
                ),
            )

        try:
            summary = render_live_internal(
                lines,
                self._app.config,
                sink,
                unit=unit,
                synthesis=resolved_synthesis,
                on_progress=progress,
            )
        except ReadioError:
            raise
        except Exception as error:
            raise translate_exception(
                error,
                error_type=ExecutionError,
                code="speech.live_render_failed",
            ) from error

        self._notify(handler, ReadioEvent(kind="operation.completed", operation="render_live"))
        return RenderResult(plan=None, summary=summary)

    def speak_live(
        self,
        lines: Iterable[str],
        *,
        synthesis: SynthesisRequest | None = None,
        unit: str | None = None,
        on_event: EventHandler | None = None,
    ) -> RenderResult:
        """Play live text through a Readio-owned playback sink."""
        from ..audio import PlaybackSink

        try:
            with PlaybackSink(self._app.config.reader) as sink:
                result = self.render_live(
                    lines,
                    sink,
                    synthesis=synthesis,
                    unit=unit,
                    on_event=on_event,
                )
                sink.finish()
        except ReadioError:
            raise
        except Exception as error:
            raise translate_exception(
                error,
                error_type=ExecutionError,
                code="speech.playback_failed",
            ) from error
        return result

    def _play(
        self,
        request: PlanRequest,
        *,
        on_event: EventHandler | None,
    ) -> RenderResult:
        from ..audio import PlaybackSink

        resolved = self._resolve_execution(request)
        handler = self._handler(on_event)
        self._notify(handler, ReadioEvent(kind="operation.started", operation=request.operation))
        try:
            with PlaybackSink(self._app.config.reader) as sink:
                execution_result = self._execute(
                    resolved,
                    sink,
                    operation=request.operation,
                    handler=handler,
                )
                sink.finish()
        except ReadioError:
            raise
        except Exception as error:
            raise translate_exception(
                error,
                error_type=ExecutionError,
                code="speech.playback_failed",
            ) from error
        self._notify(handler, ReadioEvent(kind="operation.completed", operation=request.operation))
        return self._result(resolved.plan, execution_result)

    def _execute(
        self,
        resolved: ResolvedExecutionV2,
        sink: AudioSink,
        *,
        operation: str,
        handler: EventHandler | None,
    ) -> BoundedRenderResult:
        def progress(event: RenderProgress) -> None:
            self._notify(
                handler,
                ReadioEvent(
                    kind="progress",
                    operation=operation,
                    stage="synthesis",
                    progress_kind="unit.completed",
                    total=event.total_units,
                    sample_count=event.sample_count,
                    sample_rate=event.sample_rate,
                    audio_seconds=(
                        event.sample_count / event.sample_rate if event.sample_rate else None
                    ),
                ),
            )

        def phase(message: str) -> None:
            lowered = message.lower()
            stage: EventStage = "composition" if "compos" in lowered else "output"
            self._notify(
                handler,
                ReadioEvent(
                    kind="stage.started",
                    operation=operation,
                    stage=stage,
                    message=message,
                ),
            )

        try:
            return execute_bounded_v2(
                resolved,
                sink,
                on_progress=progress if handler is not None else None,
                on_phase=phase if handler is not None else None,
            )
        except ReadioError:
            raise
        except Exception as error:
            raise translate_exception(
                error,
                error_type=ExecutionError,
                code="speech.render_failed",
            ) from error

    def _resolve_execution(self, request: PlanRequest) -> ResolvedExecutionV2:
        try:
            resolved = resolve_execution_v2(self._app.config, request)
        except ReadioError:
            raise
        except Exception as error:
            raise translate_exception(
                error,
                error_type=ResolutionError,
                code="speech.plan_failed",
            ) from error
        if not resolved.plan.ok:
            diagnostics = tuple(Diagnostic.from_plan(item) for item in resolved.plan.diagnostics)
            if any(item.code == "output_exists" for item in diagnostics):
                raise PlannedOutputError(
                    "the requested output already exists",
                    code="output.exists",
                    plan=resolved.plan,
                    diagnostics=diagnostics,
                )
            if any(item.code == "encoder_unavailable" for item in diagnostics):
                raise PlannedOutputError(
                    "the requested output encoder is unavailable",
                    code="output.encoder_unavailable",
                    plan=resolved.plan,
                    diagnostics=diagnostics,
                )
            raise PlanNotExecutableError(
                "speech request cannot be executed",
                plan=resolved.plan,
                diagnostics=diagnostics,
            )
        return resolved

    def _sink_request(self, request: PlanRequest) -> PlanRequest:
        return replace(
            request,
            output=replace(
                request.output,
                mode="playback",
                requested_format=None,
                requested_path=None,
                force=False,
                bitrate=None,
            ),
        )

    def _handler(self, on_event: EventHandler | None) -> EventHandler | None:
        return compose_event_handlers(self._app.on_event, on_event)

    def _notify(self, handler: EventHandler | None, event: ReadioEvent) -> None:
        if handler is None:
            return
        try:
            handler(event)
        except ReadioError:
            raise
        except Exception as error:
            raise translate_exception(
                error,
                error_type=ExecutionError,
                code="event.handler_failed",
            ) from error

    def _result(
        self,
        plan: ResolvedPlan | None,
        execution_result: BoundedRenderResult,
        *,
        output_path: Path | None = None,
        manifest_path: Path | None = None,
    ) -> RenderResult:
        diagnostics = tuple(
            Diagnostic.from_plan(item) for item in (plan.diagnostics if plan is not None else ())
        )
        return RenderResult(
            plan=plan,
            summary=execution_result.summary,
            output_path=output_path,
            manifest_path=manifest_path,
            diagnostics=diagnostics,
            audio_format=(
                plan.output.format if plan is not None and output_path is not None else None
            ),
            manifest_schema=(RENDER_MANIFEST_SCHEMA_V2 if manifest_path is not None else None),
        )


__all__ = ["SpeechService"]
