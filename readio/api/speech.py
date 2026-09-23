"""One-shot and live speech operations exposed through :mod:`readio.api`."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ..audio import RenderProgress
from ..errors import ManifestError, ReadioError
from ..execution import BoundedRenderResult, ResolvedExecutionV2, execute_bounded_v2
from ..formats import AudioFormat
from ..manifest import build_render_manifest_v2, manifest_path_for, write_render_manifest
from ..plan import resolve_execution_v2, resolve_plan_v2
from ..reader import render_live as render_live_internal
from ..synthesis import resolve_synthesis_request
from ..wave import atomic_audio_path, create_audio_sink
from .errors import (
    ExecutionError,
    InvalidRequestError,
    OutputError,
    ResolutionError,
    translate_exception,
)
from .events import EventHandler, ReadioEvent, compose_event_handlers
from .types import (
    Diagnostic,
    PlanRequest,
    RenderResult,
    ResolvedPlan,
    SynthesisRequest,
)

if TYPE_CHECKING:
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
            with atomic_audio_path(output, force=plan.output.force) as temporary, create_audio_sink(
                temporary, cast(AudioFormat, audio_format)
            ) as sink:
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
            error_type = OutputError if isinstance(error, (FileExistsError, OSError)) else ExecutionError
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
        request = synthesis or SynthesisRequest()
        handler = self._handler(on_event)
        self._notify(handler, ReadioEvent(kind="operation.started", operation="render_live"))

        def progress(event: RenderProgress) -> None:
            self._notify(
                handler,
                ReadioEvent(
                    kind="progress",
                    operation="render_live",
                    completed=event.completed_units,
                    total=event.total_units,
                    sample_count=event.sample_count,
                    sample_rate=event.sample_rate,
                    audio_seconds=(event.sample_count / event.sample_rate if event.sample_rate else None),
                ),
            )

        try:
            resolved_synthesis = resolve_synthesis_request(self._app.config, request)
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
                    completed=event.completed_units,
                    total=event.total_units,
                    sample_count=event.sample_count,
                    sample_rate=event.sample_rate,
                    audio_seconds=(event.sample_count / event.sample_rate if event.sample_rate else None),
                ),
            )

        def phase(message: str) -> None:
            lowered = message.lower()
            stage = "composition" if "compos" in lowered else "output"
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
            diagnostics = tuple(
                Diagnostic.from_plan(item).to_dict() for item in resolved.plan.diagnostics
            )
            if any(item["code"] == "output_exists" for item in diagnostics):
                raise OutputError(
                    "the requested output already exists",
                    code="output.exists",
                    details={"diagnostics": list(diagnostics)},
                )
            if any(item["code"] == "encoder_unavailable" for item in diagnostics):
                raise OutputError(
                    "the requested output encoder is unavailable",
                    code="output.encoder_unavailable",
                    details={"diagnostics": list(diagnostics)},
                )
            raise ExecutionError(
                "speech request cannot be executed",
                code="speech.plan_not_executable",
                details={"diagnostics": list(diagnostics)},
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
            Diagnostic.from_plan(item)
            for item in (plan.diagnostics if plan is not None else ())
        )
        return RenderResult(
            plan=plan,
            summary=execution_result.summary,
            output_path=output_path,
            manifest_path=manifest_path,
            diagnostics=diagnostics,
        )


__all__ = ["SpeechService"]
