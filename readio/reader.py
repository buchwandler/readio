"""Readio-owned request execution for bounded and live speech."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from typing import Any

import numpy as np

from .audio import (
    AudioSink,
    PlaybackSink,
    RenderProgress,
    RenderProgressCallback,
    RenderSummary,
)
from .config import ReaderSettings, ReadioConfig, default_config
from .document import InputDocument, canonicalize_document, document_from_text
from .errors import RenderError
from .plan import InputRequest, OutputRequest, PlanRequest, SynthesisRequest
from .synthesis import ResolvedSynthesis, resolve_synthesis
from .text import iter_live_paragraphs

logger = logging.getLogger(__name__)


def _config(config: ReadioConfig | ReaderSettings) -> ReadioConfig:
    return config if isinstance(config, ReadioConfig) else replace(default_config(), reader=config)


def prepare_input_document(document: InputDocument) -> InputDocument:
    return canonicalize_document(document)


def _synthesis_request(synthesis: ResolvedSynthesis) -> SynthesisRequest:
    return SynthesisRequest(
        voice_level=synthesis.voice_level,
        language=synthesis.language,
        model=synthesis.model,
        model_source=synthesis.source,
        quality=synthesis.quality,
        voice=synthesis.voice,
        speaker=synthesis.speaker,
        lexicons=synthesis.lexicons,
        spacy=synthesis.spacy,
        short_sentence=synthesis.short_sentence,
        g2p_fallback=synthesis.g2p_fallback,
        lexicon_data_policy=synthesis.lexicon_data_policy,
        language_detection=synthesis.language_detection,
        detect_languages=synthesis.detect_languages,
        allow_experimental=synthesis.allow_experimental,
        speed=synthesis.speed,
        pause_mode=synthesis.pause_mode,
        unit=synthesis.unit,
        engine=synthesis.engine,
    )


def render_from_plan_v2(
    plan: Any,
    document: InputDocument,
    sink: AudioSink,
    *,
    selector: str = "all",
    on_progress: RenderProgressCallback | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> RenderSummary:
    """Execute a resolved plan-v2 bundle without re-resolving its selection."""
    from .execution import ResolvedExecutionV2, execute_render_v2

    if not isinstance(plan, ResolvedExecutionV2):
        raise RenderError(
            "render_from_plan_v2 requires ResolvedExecutionV2; "
            "resolve with resolve_execution_v2 first"
        )
    logger.info(
        "render.v2.start format=%s selector=%s engine=%s",
        document.format,
        selector,
        plan.plan.render.engine if plan.plan.render else "unknown",
    )
    return execute_render_v2(
        plan,
        sink,
        on_progress=on_progress,
        on_phase=on_phase,
    ).summary


def render_text(
    text: str | InputDocument,
    cfg: ReadioConfig | ReaderSettings,
    sink: AudioSink,
    *,
    selector: str = "all",
    unit: str | None = None,
    ssmd_voice_bindings: Mapping[str, str] | None = None,
    synthesis: ResolvedSynthesis | None = None,
    on_progress: RenderProgressCallback | None = None,
) -> RenderSummary:
    from .plan import resolve_execution_v2

    config = _config(cfg)
    document = text if isinstance(text, InputDocument) else document_from_text(text)
    document = prepare_input_document(document)
    if not document.text.strip():
        raise ValueError("no text to read")
    if document.format == "text" and selector == "all" and not ssmd_voice_bindings:
        return render_live(
            document.text.splitlines(keepends=True),
            config,
            sink,
            unit=unit,
            synthesis=synthesis,
            on_progress=on_progress,
        )
    resolved_synthesis = synthesis or resolve_synthesis(config)
    request = PlanRequest(
        operation="render",
        input=InputRequest(document=document, selector=selector, source_kind="literal"),
        synthesis=replace(
            _synthesis_request(resolved_synthesis), unit=unit or resolved_synthesis.unit
        ),
        output=OutputRequest(mode="playback"),
        voice_bindings=dict(ssmd_voice_bindings or {}),
    )
    execution = resolve_execution_v2(config, request)
    if not execution.plan.ok:
        details = "; ".join(item.message for item in execution.plan.diagnostics)
        raise RenderError(f"render plan is not executable: {details}")
    return render_from_plan_v2(
        execution,
        document,
        sink,
        selector=selector,
        on_progress=on_progress,
    )


def _compose_live_speed(audio: np.ndarray, sample_rate: int, speed: float) -> np.ndarray:
    if speed == 1.0:
        return audio
    from audiocompose import (
        AudioBufferSource,
        AudioClip,
        AudioJob,
        Composer,
        LoudnessPolicy,
        OutputPolicy,
        Tempo,
    )

    job = AudioJob(
        items=(
            AudioClip(
                id="live-speech",
                source=AudioBufferSource(audio=audio, sample_rate=sample_rate),
                operations=(Tempo(speed),),
                metadata={"readio.mode": "live"},
            ),
        ),
        output=OutputPolicy(
            sample_rate=sample_rate,
            loudness=LoudnessPolicy(
                target_lufs=None,
                true_peak_ceiling_dbtp=None,
                collect_metrics=False,
            ),
        ),
        producer={"name": "readio.live-playback"},
    )
    return Composer().compose(job).audio


def render_live(
    lines: Iterable[str],
    cfg: ReadioConfig | ReaderSettings,
    sink: AudioSink,
    *,
    unit: str | None = None,
    synthesis: ResolvedSynthesis | None = None,
    on_progress: RenderProgressCallback | None = None,
) -> RenderSummary:
    """Stream live text through one VoiceRender session, composing only non-neutral speed."""
    from contextlib import ExitStack

    from voicerender import (
        EngineApiIncompatibleError,
        EngineRequest,
        EngineUnavailableError,
        SpeechRequest,
        open_session,
        synthesize,
    )
    from voicerender.errors import VoiceRenderError

    from .integrations.voicerender import _resolve_readio_request, _resolve_voice_request

    config = _config(cfg)

    def translate_voice_error(error: VoiceRenderError) -> RenderError:
        cause_details = getattr(error, "details", {})
        return RenderError(
            f"live VoiceRender error: {error}",
            code=str(getattr(error, "code", "render.voice_render_failed")),
            details={
                "cause_type": type(error).__name__,
                "cause_code": getattr(error, "code", None),
                "cause_details": dict(cause_details) if isinstance(cause_details, Mapping) else {},
            },
        )

    if synthesis is None:
        language = config.reader.lang
        target_id = None
        speaker = None
        voice = config.reader.voice
        engine = config.reader.engine
        engine_options: Mapping[str, Any] = {}
        speed = float(config.reader.speed)
        offline = False
        refresh = False
    else:
        language = synthesis.language
        target_id = synthesis.model
        speaker = synthesis.speaker
        voice = synthesis.voice
        engine = synthesis.engine
        engine_options = synthesis.engine_options
        speed = synthesis.speed
        offline = synthesis.discovery_offline
        refresh = synthesis.discovery_refreshed
    request = EngineRequest(
        engine=engine,
        target_id=target_id,
        language=language,
        voice=voice,
        speaker=speaker,
        options={"speed": speed},
        engine_options=dict(engine_options),
        offline=offline,
        refresh=refresh,
    )
    try:
        selection, diagnostics, adapter = _resolve_voice_request(request)
    except Exception as exc:
        raise translate_voice_error(exc) from exc
    errors = [item.message for item in diagnostics if getattr(item, "severity", None) == "error"]
    if errors:
        raise RenderError("live VoiceRender selection failed: " + "; ".join(errors))
    saw_text = False
    sample_rate = 0
    channels = 0
    sample_count = 0
    completed = 0
    try:
        with ExitStack() as stack:
            try:
                session_context = (
                    adapter.open(selection) if adapter is not None else open_session(selection)
                )
                session = stack.enter_context(session_context)
            except (EngineApiIncompatibleError, EngineUnavailableError):
                if adapter is not None:
                    raise
                selection, diagnostics, adapter = _resolve_readio_request(request)
                errors = [
                    item.message
                    for item in diagnostics
                    if getattr(item, "severity", None) == "error"
                ]
                if errors:
                    raise RenderError("live engine selection failed: " + "; ".join(errors))
                session = stack.enter_context(adapter.open(selection))
            for paragraph in iter_live_paragraphs(lines):
                saw_text = True
                speech_request = SpeechRequest(
                    id=f"live-{completed + 1}",
                    text=paragraph,
                    language=language,
                    voice=selection.voice,
                    speaker=selection.speaker,
                    options=dict(selection.options),
                )
                rendered = (
                    session.synthesize(speech_request)
                    if adapter is not None
                    else synthesize(session, speech_request)
                )
                audio = np.asarray(rendered.audio, dtype=np.float32)
                audio = _compose_live_speed(audio, rendered.sample_rate, speed)
                rendered_channels = 1 if audio.ndim == 1 else int(audio.shape[1])
                if sample_count and (
                    rendered.sample_rate != sample_rate or rendered_channels != channels
                ):
                    raise RenderError("live chunks must use the same sample rate and channel count")
                sample_rate = rendered.sample_rate
                channels = rendered_channels
                sink.write(audio, sample_rate)
                sample_count += int(audio.shape[0])
                completed += 1
                if on_progress is not None:
                    on_progress(RenderProgress(completed, None, sample_count, sample_rate))
    except (VoiceRenderError, EngineApiIncompatibleError, EngineUnavailableError) as exc:
        raise translate_voice_error(exc) from exc
    if not saw_text:
        raise ValueError("no text to read")
    return RenderSummary(
        sample_rate=sample_rate,
        sample_count=sample_count,
        channels=channels,
        document_metadata={},
        markers=(),
    )


def speak_text(
    text: str | InputDocument,
    cfg: ReadioConfig | ReaderSettings,
    *,
    selector: str = "all",
    unit: str | None = None,
    ssmd_voice_bindings: Mapping[str, str] | None = None,
    synthesis: ResolvedSynthesis | None = None,
) -> None:
    config = _config(cfg)
    logger.info("playback.start mode=text selector=%s", selector)
    with PlaybackSink(config.reader) as sink:
        render_text(
            text,
            config,
            sink,
            selector=selector,
            unit=unit,
            ssmd_voice_bindings=ssmd_voice_bindings,
            synthesis=synthesis,
        )
        sink.finish()


def speak_live(
    lines: Iterable[str],
    cfg: ReadioConfig | ReaderSettings,
    *,
    unit: str | None = None,
    synthesis: ResolvedSynthesis | None = None,
) -> None:
    config = _config(cfg)
    logger.info("playback.start mode=live unit=%s", unit or "default")
    with PlaybackSink(config.reader) as sink:
        render_live(lines, config, sink, unit=unit, synthesis=synthesis)
        sink.finish()
