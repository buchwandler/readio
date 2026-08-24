from __future__ import annotations

from collections.abc import Iterable
from contextlib import AbstractContextManager
from typing import Any

from .audio import AudioSink, PlaybackSink, RenderSummary, render_prepared
from .config import ReaderSettings, ReadioConfig
from .document import InputDocument, document_from_text
from .errors import InputError
from .markdown import markdown_to_speech
from .ssmd import build_ssmd_render_config, preflight_ssmd
from .text import iter_live_paragraphs


class SelectionError(ValueError):
    pass


def prepare_input_document(document: InputDocument) -> InputDocument:
    if document.format != "markdown":
        return document
    try:
        text = markdown_to_speech(document.text)
    except Exception as exc:
        source = f" {document.source_path}" if document.source_path else ""
        raise InputError(f"failed to parse Markdown{source}: {exc}", source_path=document.source_path) from exc
    return InputDocument(text=text, source_path=document.source_path, format="text")
def pipeline_config_for_document(
    document: InputDocument,
    cfg: ReadioConfig,
) -> Any:
    from pykokoro import GenerationConfig, PipelineConfig, SSMDRenderConfig

    generation = GenerationConfig(
        lang=cfg.reader.lang,
        speed=cfg.reader.speed,
        pause_mode=cfg.reader.pause_mode,
    )
    ssmd = (
        build_ssmd_render_config(document.text, cfg)
        if document.format == "ssmd"
        else SSMDRenderConfig()
    )
    return PipelineConfig(voice=cfg.reader.voice, generation=generation, ssmd=ssmd)


def _build_pipeline(
    document: InputDocument, cfg: ReadioConfig | ReaderSettings
) -> AbstractContextManager[Any]:
    from pykokoro import GenerationConfig, KokoroPipeline, PipelineConfig

    if isinstance(cfg, ReadioConfig):
        if document.format == "ssmd" and cfg.ssmd.validate_before_render:
            preflight_ssmd(document.text, cfg, source_path=document.source_path)
        return KokoroPipeline(pipeline_config_for_document(document, cfg))

    generation = GenerationConfig(
        lang=cfg.lang,
        speed=cfg.speed,
        pause_mode=cfg.pause_mode,
    )
    return KokoroPipeline(PipelineConfig(voice=cfg.voice, generation=generation))


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
) -> RenderSummary:
    document = text if isinstance(text, InputDocument) else document_from_text(text)
    document = prepare_input_document(document)
    if not document.text.strip():
        raise ValueError("no text to read")
    reader_cfg = cfg.reader if isinstance(cfg, ReadioConfig) else cfg
    effective_unit = unit or reader_cfg.unit
    prepare_unit = "paragraph" if selector != "all" else effective_unit
    with (
        _build_pipeline(document, cfg) as pipeline,
        pipeline.prepare_units(document.text, unit=prepare_unit) as prepared,
    ):
        return render_prepared(prepared, sink, indices=_selected_indices(prepared, selector))


def render_live(
    lines: Iterable[str],
    cfg: ReadioConfig | ReaderSettings,
    sink: AudioSink,
    *,
    unit: str | None = None,
) -> RenderSummary:
    reader_cfg = cfg.reader if isinstance(cfg, ReadioConfig) else cfg
    effective_unit = unit or reader_cfg.unit
    saw_text = False
    sample_rate = 0
    sample_count = 0
    channels = 0
    metadata: dict[str, Any] = {}
    markers: list[dict[str, Any]] = []

    with _build_pipeline(document_from_text(""), cfg) as pipeline:
        for paragraph in iter_live_paragraphs(lines):
            saw_text = True
            with pipeline.prepare_units(paragraph, unit=effective_unit) as prepared:
                summary = render_prepared(prepared, sink)
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
) -> None:
    playback_cfg = cfg.reader if isinstance(cfg, ReadioConfig) else cfg
    with PlaybackSink(playback_cfg) as sink:
        render_text(text, cfg, sink, selector=selector, unit=unit)
        sink.finish()


def speak_live(
    lines: Iterable[str], cfg: ReadioConfig | ReaderSettings, *, unit: str | None = None
) -> None:
    playback_cfg = cfg.reader if isinstance(cfg, ReadioConfig) else cfg
    with PlaybackSink(playback_cfg) as sink:
        render_live(lines, cfg, sink, unit=unit)
        sink.finish()
