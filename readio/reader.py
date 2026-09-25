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
from .document import InputDocument, document_from_text
from .errors import InputError, RenderError
from .markdown import markdown_to_speech
from .plan import InputRequest, OutputRequest, PlanRequest, SynthesisRequest
from .synthesis import ResolvedSynthesis, resolve_synthesis
from .text import iter_live_paragraphs

logger = logging.getLogger(__name__)


def _config(config: ReadioConfig | ReaderSettings) -> ReadioConfig:
    return config if isinstance(config, ReadioConfig) else replace(default_config(), reader=config)


def prepare_input_document(document: InputDocument) -> InputDocument:
    if document.format != "markdown":
        return document
    try:
        text = markdown_to_speech(document.text)
    except Exception as exc:
        source = f" {document.source_path}" if document.source_path else ""
        raise InputError(
            f"failed to parse Markdown{source}: {exc}", source_path=document.source_path
        ) from exc
    return InputDocument(text=text, source_path=document.source_path, format="text")


def _synthesis_request(synthesis: ResolvedSynthesis) -> SynthesisRequest:
    return SynthesisRequest(
        language=synthesis.language,
        model=synthesis.model,
        model_source=synthesis.source,
        quality=synthesis.quality,
        voice=synthesis.voice,
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
    from .execution import ResolvedExecutionV2, execute_bounded_v2

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
    return execute_bounded_v2(
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


def render_live(
    lines: Iterable[str],
    cfg: ReadioConfig | ReaderSettings,
    sink: AudioSink,
    *,
    unit: str | None = None,
    synthesis: ResolvedSynthesis | None = None,
    on_progress: RenderProgressCallback | None = None,
) -> RenderSummary:
    """Stream independent paragraphs through one Readio engine session."""
    from .engines.base import SpeechRequest
    from .engines.registry import get_engine
    from .engines.selection import EngineRequest

    config = _config(cfg)
    resolved = synthesis or resolve_synthesis(config)
    engine_id = resolved.engine or config.reader.engine
    adapter = get_engine(engine_id)
    if not adapter.capabilities().supports_live:
        raise RenderError(f"live rendering is not supported by engine {engine_id!r}")
    options: dict[str, Any] = {
        "speed": resolved.speed,
        "rate": resolved.speed,
        "pause_mode": resolved.pause_mode,
        "short_sentence": resolved.short_sentence,
        "allow_experimental": resolved.allow_experimental,
    }
    optional_options = {
        "model_source": resolved.source,
        "quality": resolved.quality,
        "lexicons": resolved.lexicons,
        "g2p_fallback": resolved.g2p_fallback,
        "lexicon_data_policy": resolved.lexicon_data_policy,
        "language_detection": resolved.language_detection,
        "detect_languages": resolved.detect_languages,
    }
    options.update({key: value for key, value in optional_options.items() if value is not None})
    selection, diagnostics = adapter.resolve(
        EngineRequest(
            engine=engine_id,
            target_id=resolved.model,
            language=resolved.language,
            voice=resolved.voice,
            options=options,
        )
    )
    validate = getattr(adapter, "validate_selection", None)
    if validate is not None:
        diagnostics = (*diagnostics, *validate(selection))
    errors = [item.message for item in diagnostics if getattr(item, "severity", None) == "error"]
    if errors:
        raise RenderError("live synthesis selection failed: " + "; ".join(errors))
    target_metadata = getattr(adapter, "target_metadata", None)
    if target_metadata is not None:
        selection = replace(selection, metadata=dict(target_metadata(selection)))

    saw_text = False
    sample_rate = 0
    channels = 0
    sample_count = 0
    completed = 0
    with adapter.open(selection) as session:
        for paragraph in iter_live_paragraphs(lines):
            saw_text = True
            rendered = session.synthesize(
                SpeechRequest(
                    id=f"live-{completed + 1}",
                    text=paragraph,
                    language=resolved.language,
                    voice=selection.voice,
                    speaker=selection.speaker,
                    options=dict(selection.options),
                )
            )
            audio = np.asarray(rendered.audio)
            rendered_channels = 1 if audio.ndim == 1 else int(audio.shape[1])
            if sample_count and (
                rendered.sample_rate != sample_rate or rendered_channels != channels
            ):
                raise RenderError("live chunks must use the same sample rate and channel count")
            sample_rate = rendered.sample_rate
            channels = rendered_channels
            sink.write(audio, rendered.sample_rate)
            sample_count += int(audio.shape[0])
            completed += 1
            if on_progress is not None:
                on_progress(RenderProgress(completed, None, sample_count, sample_rate))
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
