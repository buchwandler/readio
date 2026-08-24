from __future__ import annotations

from collections.abc import Iterable
from contextlib import AbstractContextManager
from typing import Any

from .audio import AudioSink, PlaybackSink, RenderSummary, render_prepared
from .config import ReaderConfig
from .text import iter_live_paragraphs


class SelectionError(ValueError):
    pass


def _build_pipeline(cfg: ReaderConfig) -> AbstractContextManager[Any]:
    from pykokoro import GenerationConfig, KokoroPipeline, PipelineConfig

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
    text: str,
    cfg: ReaderConfig,
    sink: AudioSink,
    *,
    selector: str = "all",
    unit: str | None = None,
) -> RenderSummary:
    if not text.strip():
        raise ValueError("no text to read")
    effective_unit = unit or cfg.unit
    prepare_unit = "paragraph" if selector != "all" else effective_unit
    with (
        _build_pipeline(cfg) as pipeline,
        pipeline.prepare_units(text, unit=prepare_unit) as prepared,
    ):
        return render_prepared(prepared, sink, indices=_selected_indices(prepared, selector))


def render_live(
    lines: Iterable[str],
    cfg: ReaderConfig,
    sink: AudioSink,
    *,
    unit: str | None = None,
) -> RenderSummary:
    effective_unit = unit or cfg.unit
    saw_text = False
    sample_rate = 0
    sample_count = 0
    channels = 0
    metadata: dict[str, Any] = {}
    markers: list[dict[str, Any]] = []

    with _build_pipeline(cfg) as pipeline:
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
    text: str,
    cfg: ReaderConfig,
    *,
    selector: str = "all",
    unit: str | None = None,
) -> None:
    with PlaybackSink(cfg) as sink:
        render_text(text, cfg, sink, selector=selector, unit=unit)
        sink.finish()


def speak_live(lines: Iterable[str], cfg: ReaderConfig, *, unit: str | None = None) -> None:
    with PlaybackSink(cfg) as sink:
        render_live(lines, cfg, sink, unit=unit)
        sink.finish()
