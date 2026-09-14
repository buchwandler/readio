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




def tokenizer_config_for_synthesis(synthesis: object) -> Any:
    """Build a backend-specific tokenizer override through the registry."""
    from .backends import get_backend

    backend = get_backend(getattr(synthesis, "engine", "pykokoro"))
    return backend.tokenizer_config_for_synthesis(synthesis)
def short_sentence_config_for_synthesis(synthesis: object) -> Any:
    """Build a backend-specific short-sentence configuration."""
    from .backends import get_backend

    backend = get_backend(getattr(synthesis, "engine", "pykokoro"))
    return backend.short_sentence_config_for_synthesis(synthesis)



def language_detection_config_for_synthesis(
    synthesis: object,
    document: InputDocument | None = None,
 ) -> Any:
    """Build a backend-specific language-detection configuration."""
    from .backends import get_backend

    backend = get_backend(getattr(synthesis, "engine", "pykokoro"))
    return backend.language_detection_config_for_synthesis(synthesis, document)

def pipeline_config_for_document(
    document: InputDocument,
    cfg: ReadioConfig,
    *,
    ssmd_voice_bindings: Mapping[str, str] | None = None,
    synthesis: ResolvedSynthesis | None = None,
 ) -> Any:
    """Build a backend configuration through the selected adapter."""
    from .backends import get_backend
    from .synthesis import resolve_synthesis

    resolved = synthesis or resolve_synthesis(cfg)
    backend = get_backend(resolved.engine)
    return backend.pipeline_config_for_document(
        document,
        cfg,
        ssmd_voice_bindings=dict(ssmd_voice_bindings or {}),
        synthesis=resolved,
    )


def pipeline_config_from_plan(
    plan: ReadioPlan,
    document: InputDocument,
 ) -> Any:
    """Build a concrete backend configuration from a resolved plan."""
    from .backends import get_backend

    synthesis = plan.synthesis
    if synthesis is None:
        raise ValueError("plan has no synthesis; cannot build pipeline config")
    backend = get_backend(synthesis.engine)
    return backend.pipeline_config_from_plan(plan, document)



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
    from .backends import get_backend

    document = prepare_input_document(document)
    if not document.text.strip():
        raise ValueError("no text to read")
    if plan.synthesis is None:
        raise ValueError("plan has no synthesis; cannot render")
    backend = get_backend(plan.synthesis.engine)
    with backend.open_session(plan, document) as pipeline:
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
    """Open the selected backend for a compatibility or planned request."""
    from .backends import get_backend

    backend = get_backend(
        getattr(synthesis, "engine", None) or getattr(cfg, "engine", "pykokoro")
    )
    if isinstance(cfg, ReadioConfig):
        resolved = synthesis or resolve_synthesis(cfg)
        return backend.open_resolved_session(
            document,
            cfg,
            ssmd_voice_bindings=dict(ssmd_voice_bindings or {}),
            synthesis=resolved,
        )
    return backend.open_legacy_session(document, cfg)



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
    """Resolve and execute non-live playback through one explicit plan."""
    logger.info("playback.start mode=text selector=%s", selector)
    if not isinstance(cfg, ReadioConfig):
        playback_cfg = cfg
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
        return

    from .plan import InputRequest, OutputRequest, PlanRequest, SynthesisRequest, resolve_plan

    resolved = synthesis or resolve_synthesis(cfg)
    document = text if isinstance(text, InputDocument) else document_from_text(text)
    request = PlanRequest(
        operation="speak",
        input=InputRequest(
            document=document,
            selector=selector,
            source_kind="literal",
        ),
        synthesis=SynthesisRequest(
            language=resolved.language,
            engine=resolved.engine,
            model=resolved.model,
            model_source=resolved.source,
            quality=resolved.quality,
            voice=resolved.voice,
            lexicons=resolved.lexicons if resolved.lexicons else None,
            clear_lexicons=resolved.lexicons == (),
            g2p_fallback=resolved.g2p_fallback,
            spacy=resolved.spacy,
            short_sentence=resolved.short_sentence,
            lexicon_data_policy=resolved.lexicon_data_policy,
            language_detection=resolved.language_detection,
            detect_languages=resolved.detect_languages,
            allow_experimental=resolved.allow_experimental,
            speed=resolved.speed,
            pause_mode=resolved.pause_mode,
            unit=unit or resolved.unit,
        ),
        output=OutputRequest(mode="playback"),
        voice_bindings=dict(ssmd_voice_bindings or {}),
    )
    plan = resolve_plan(cfg, request)
    if not plan.ok:
        raise RenderError("speak plan is not executable")
    with PlaybackSink(cfg.reader) as sink:
        render_from_plan(plan, document, sink, selector=selector)
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
