from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from functools import partial
from typing import TYPE_CHECKING, Any

from .audio import (
    AudioSink,
    PlaybackSink,
    RenderProgress,
    RenderProgressCallback,
    RenderSummary,
    render_prepared,
)
from .config import ReaderSettings, ReadioConfig
from .document import InputDocument, document_from_text
from .errors import InputError, RenderError
from .markdown import markdown_to_speech
from .ssmd import build_ssmd_render_config, language_detection_hint, preflight_ssmd
from .synthesis import ResolvedSynthesis, resolve_synthesis
from .text import iter_live_paragraphs

if TYPE_CHECKING:
    from .plan import ReadioPlan


logger = logging.getLogger(__name__)


class SelectionError(ValueError):
    pass


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


def _spacy_settings(policy: str | None) -> tuple[bool | None, str | None]:
    if policy in {None, "auto"}:
        return None, None
    if policy == "off":
        return False, None
    if policy == "required":
        policy = "sm"
    if policy in {"sm", "md", "lg", "trf"}:
        return True, policy
    raise ValueError(f"unsupported spaCy policy: {policy!r}")


def tokenizer_config_for_synthesis(synthesis: object) -> Any:
    """Build the explicit PyKokoro tokenizer override, if one is needed."""
    from pykokoro.tokenizer import TokenizerConfig

    use_spacy, spacy_model_size = _spacy_settings(getattr(synthesis, "spacy", None))
    values = {
        "lexicons": getattr(synthesis, "lexicons", None),
        "fallback": getattr(synthesis, "g2p_fallback", None),
        "lexicon_data_policy": getattr(synthesis, "lexicon_data_policy", None),
        "use_spacy": use_spacy,
        "spacy_model_size": spacy_model_size,
    }
    if not any(value is not None for value in values.values()):
        return None
    return TokenizerConfig(**{key: value for key, value in values.items() if value is not None})


def short_sentence_config_for_synthesis(synthesis: object) -> Any:
    policy = getattr(synthesis, "short_sentence", None)
    if policy in {None, "auto"}:
        return None
    from pykokoro.short_sentence_handler import ShortSentenceConfig

    if policy == "off":
        return ShortSentenceConfig(enabled=False)
    if policy in {"wrap", "phrase", "randomized-phrase"}:
        return ShortSentenceConfig(enabled=True, resolve_mode=policy)
    raise ValueError(f"unsupported short-sentence policy: {policy!r}")


def language_detection_config_for_synthesis(
    synthesis: object,
    document: InputDocument | None = None,
) -> Any:
    """Build PyKokoro language-detection configuration from resolved intent."""
    mode = getattr(synthesis, "language_detection", None)
    languages = getattr(synthesis, "detect_languages", None)
    if mode is None and document is not None and document.format == "ssmd":
        hint = language_detection_hint(document.text)
        if hint is not None:
            mode, languages = hint
    if mode is None:
        return None
    from pykokoro import LanguageDetectionConfig

    return LanguageDetectionConfig(mode=mode, languages=tuple(languages or ()))


def pipeline_config_for_document(
    document: InputDocument,
    cfg: ReadioConfig,
    *,
    ssmd_voice_bindings: Mapping[str, str] | None = None,
    synthesis: ResolvedSynthesis | None = None,
) -> Any:
    from pykokoro import GenerationConfig, PipelineConfig, SSMDRenderConfig

    resolved = synthesis or resolve_synthesis(cfg)
    generation = GenerationConfig(
        lang=resolved.language,
        speed=resolved.speed,
        pause_mode=resolved.pause_mode,
    )
    tokenizer_config = tokenizer_config_for_synthesis(resolved)
    language_detection = language_detection_config_for_synthesis(resolved, document)
    ssmd = (
        build_ssmd_render_config(document.text, cfg, ssmd_voice_bindings, resolved)
        if document.format == "ssmd"
        else SSMDRenderConfig()
    )
    return PipelineConfig(
        voice=resolved.voice,
        model_source=resolved.source,
        model_variant=resolved.model,
        model_quality=resolved.quality,
        allow_experimental_frontend=resolved.allow_experimental,
        generation=generation,
        language_detection=language_detection,
        tokenizer_config=tokenizer_config,
        short_sentence_config=short_sentence_config_for_synthesis(resolved),
        ssmd=ssmd,
    )


def pipeline_config_from_plan(
    plan: ReadioPlan,
    document: InputDocument,
) -> Any:
    """Build a PipelineConfig from a resolved ReadioPlan.

    All values come from the plan — no automatic selection is re-run.
    """
    from pykokoro import GenerationConfig, PipelineConfig, SSMDRenderConfig

    synthesis = plan.synthesis
    if synthesis is None:
        raise ValueError("plan has no synthesis; cannot build pipeline config")
    generation = GenerationConfig(
        lang=synthesis.language,
        speed=synthesis.speed,
        pause_mode=synthesis.pause_mode,
    )
    tokenizer_config = tokenizer_config_for_synthesis(synthesis)
    language_detection = language_detection_config_for_synthesis(synthesis)

    # Build SSMD render config from plan bindings
    if document.format == "ssmd" and plan.ssmd.enabled:
        from pykokoro import SSMDRenderConfig as _SSMDRC

        provider = plan.ssmd.provider or "kokoro"
        bindings_map: dict[str, dict[str, str]] = {}
        if plan.ssmd.bindings:
            provider_bindings: dict[str, str] = {}
            for binding in plan.ssmd.bindings:
                provider_bindings[binding.reference] = binding.voice
            bindings_map[provider] = provider_bindings
        ssmd = _SSMDRC(
            provider=provider,
            voice_bindings=bindings_map,
            missing_voice="error",
        )
    else:
        ssmd = SSMDRenderConfig()

    model = synthesis.model
    return PipelineConfig(
        voice=model.voice,
        model_source=model.source,
        model_variant=model.id,
        model_quality=model.quality,
        allow_experimental_frontend=synthesis.allow_experimental,
        generation=generation,
        language_detection=language_detection,
        tokenizer_config=tokenizer_config,
        ssmd=ssmd,
        short_sentence_config=short_sentence_config_for_synthesis(synthesis),
    )


def render_from_plan(
    plan: ReadioPlan,
    document: InputDocument,
    sink: AudioSink,
    *,
    selector: str = "all",
    on_progress: RenderProgressCallback | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> RenderSummary:
    """Execute a resolved ReadioPlan exactly.

    The pipeline configuration is derived from the plan via
    ``pipeline_config_from_plan``; no synthesis selection is re-run here.
    """
    logger.info(
        "render.start format=%s selector=%s source=%s",
        document.format,
        selector,
        document.source_path or "stdin",
    )
    from pykokoro import KokoroPipeline

    document = prepare_input_document(document)
    if not document.text.strip():
        raise ValueError("no text to read")
    if plan.synthesis is None:
        raise ValueError("plan has no synthesis; cannot render")
    config = pipeline_config_from_plan(plan, document)
    with KokoroPipeline(config) as pipeline:
        unit = plan.synthesis.unit
        prepare_unit = "paragraph" if selector != "all" else unit
        with pipeline.prepare_units(document.text, unit=prepare_unit) as prepared:
            indices = _selected_indices(prepared, selector)
            if on_progress is None:
                summary = render_prepared(prepared, sink, indices=indices)
            else:
                summary = render_prepared(
                    prepared,
                    sink,
                    indices=indices,
                    on_progress=on_progress,
                )
    if on_phase is not None and plan.output.format:
        on_phase(f"Finalizing {plan.output.format.upper()}")
    if summary.sample_count <= 0:
        raise RenderError("render produced no audio")
    return summary


def _build_pipeline(
    document: InputDocument,
    cfg: ReadioConfig | ReaderSettings,
    *,
    ssmd_voice_bindings: Mapping[str, str] | None = None,
    synthesis: ResolvedSynthesis | None = None,
) -> AbstractContextManager[Any]:
    logger.info(
        "tts.load.start model=%s voice=%s",
        getattr(synthesis, "model", None) or "default",
        getattr(synthesis, "voice", None) or getattr(cfg, "reader", cfg).voice,
    )
    from pykokoro import GenerationConfig, KokoroPipeline, PipelineConfig

    if isinstance(cfg, ReadioConfig):
        resolved = synthesis or resolve_synthesis(cfg)
        if document.format == "ssmd" and cfg.ssmd.validate_before_render:
            preflight_ssmd(
                document.text,
                cfg,
                source_path=document.source_path,
                additional_bindings=ssmd_voice_bindings,
                synthesis=resolved,
            )
        return KokoroPipeline(
            pipeline_config_for_document(
                document, cfg, ssmd_voice_bindings=ssmd_voice_bindings, synthesis=resolved
            )
        )

    generation = GenerationConfig(
        lang=cfg.lang,
        speed=cfg.speed,
        pause_mode=cfg.pause_mode,
    )
    tokenizer_config = tokenizer_config_for_synthesis(cfg)
    short_sentence_config = short_sentence_config_for_synthesis(cfg)
    return KokoroPipeline(
        PipelineConfig(
            voice=cfg.voice,
            generation=generation,
            tokenizer_config=tokenizer_config,
            short_sentence_config=short_sentence_config,
        )
    )


def _selected_indices(prepared: Any, selector: str) -> tuple[int, ...] | None:
    if selector == "all":
        return None
    units = prepared.units
    if not units:
        raise SelectionError("the input produced no readable paragraphs")
    if selector == "last-paragraph":
        return (units[-1].index,)
    if selector.startswith("paragraph:"):
        raw = selector.partition(":")[2]
        try:
            one_based = int(raw)
        except ValueError as exc:
            raise SelectionError("paragraph selector must look like paragraph:3") from exc
        if one_based <= 0 or one_based > len(units):
            raise SelectionError(
                f"paragraph {one_based} is out of range; document has {len(units)} paragraphs"
            )
        return (units[one_based - 1].index,)
    raise SelectionError("selector must be all, last-paragraph, or paragraph:N")


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
    document = text if isinstance(text, InputDocument) else document_from_text(text)
    document = prepare_input_document(document)
    if not document.text.strip():
        raise ValueError("no text to read")
    logger.info(
        "input.ready format=%s characters=%d source=%s",
        document.format,
        len(document.text),
        document.source_path or "stdin",
    )
    reader_cfg = cfg.reader if isinstance(cfg, ReadioConfig) else cfg
    effective_unit = unit or (synthesis.unit if synthesis is not None else reader_cfg.unit)
    prepare_unit = "paragraph" if selector != "all" else effective_unit
    if ssmd_voice_bindings is None and synthesis is None:
        pipeline_context = _build_pipeline(document, cfg)
    elif ssmd_voice_bindings is None:
        pipeline_context = _build_pipeline(document, cfg, synthesis=synthesis)
    else:
        pipeline_context = _build_pipeline(
            document, cfg, ssmd_voice_bindings=ssmd_voice_bindings, synthesis=synthesis
        )
    with (
        pipeline_context as pipeline,
        pipeline.prepare_units(document.text, unit=prepare_unit) as prepared,
    ):
        indices = _selected_indices(prepared, selector)
        if on_progress is None:
            return render_prepared(prepared, sink, indices=indices)
        return render_prepared(
            prepared,
            sink,
            indices=indices,
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
    reader_cfg = cfg.reader if isinstance(cfg, ReadioConfig) else cfg
    effective_unit = unit or (synthesis.unit if synthesis is not None else reader_cfg.unit)
    logger.info("render.live.start unit=%s", effective_unit)
    saw_text = False
    sample_rate = 0
    sample_count = 0
    channels = 0
    metadata: dict[str, Any] = {}
    markers: list[dict[str, Any]] = []
    completed_units = 0

    def emit_paragraph_progress(
        event: RenderProgress,
        *,
        base_completed_units: int,
        base_sample_count: int,
        base_sample_rate: int,
        state: dict[str, int],
    ) -> None:
        state["completed"] = event.completed_units
        if on_progress is not None:
            on_progress(
                RenderProgress(
                    completed_units=base_completed_units + event.completed_units,
                    total_units=None,
                    sample_count=base_sample_count + event.sample_count,
                    sample_rate=event.sample_rate or base_sample_rate,
                )
            )

    with _build_pipeline(document_from_text(""), cfg, synthesis=synthesis) as pipeline:
        for paragraph in iter_live_paragraphs(lines):
            saw_text = True
            with pipeline.prepare_units(paragraph, unit=effective_unit) as prepared:
                paragraph_state = {"completed": 0}
                paragraph_callback = (
                    partial(
                        emit_paragraph_progress,
                        base_completed_units=completed_units,
                        base_sample_count=sample_count,
                        base_sample_rate=sample_rate,
                        state=paragraph_state,
                    )
                    if on_progress is not None
                    else None
                )
                summary = render_prepared(
                    prepared,
                    sink,
                    on_progress=paragraph_callback,
                )
            completed_units += paragraph_state["completed"]
            if summary.sample_count:
                if sample_count and (
                    summary.sample_rate != sample_rate or summary.channels != channels
                ):
                    raise ValueError(
                        "all rendered chunks must use the same sample rate and channel count"
                    )
                sample_rate = summary.sample_rate
                channels = summary.channels
            sample_count += summary.sample_count
            metadata.update(summary.document_metadata)
            markers.extend(
                {
                    **marker,
                    "sample_offset": int(marker["sample_offset"])
                    + sample_count
                    - summary.sample_count,
                }
                for marker in summary.markers
            )

    if not saw_text:
        raise ValueError("no text to read")
    return RenderSummary(
        sample_rate=sample_rate,
        sample_count=sample_count,
        channels=channels,
        document_metadata=metadata,
        markers=tuple(markers),
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
    logger.info("playback.start mode=text selector=%s", selector)
    playback_cfg = cfg.reader if isinstance(cfg, ReadioConfig) else cfg
    with PlaybackSink(playback_cfg) as sink:
        render_text(
            text,
            cfg,
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
    logger.info("playback.start mode=live unit=%s", unit or "default")
    playback_cfg = cfg.reader if isinstance(cfg, ReadioConfig) else cfg
    with PlaybackSink(playback_cfg) as sink:
        render_live(lines, cfg, sink, unit=unit, synthesis=synthesis)
        sink.finish()
