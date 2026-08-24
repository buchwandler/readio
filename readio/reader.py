from __future__ import annotations

from collections.abc import Iterable
from contextlib import AbstractContextManager
from typing import Any

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


def _play_rendered(prepared: Any, *, indices: tuple[int, ...] | None, cfg: ReaderConfig) -> None:
    from pykokoro.playback import SoundDevicePlayer

    player = None
    try:
        iterator = prepared.render(indices=indices)
        for result in iterator:
            try:
                if player is None:
                    channels = 1 if result.audio.ndim == 1 else int(result.audio.shape[1])
                    player = SoundDevicePlayer(
                        result.sample_rate,
                        device=cfg.device,
                        queue_size=cfg.queue_size,
                        channels=channels,
                    ).start()
                player.submit(result.audio)
            finally:
                result.release_audio()
        if player is not None:
            player.drain()
    finally:
        if player is not None:
            player.close()


def speak_text(
    text: str,
    cfg: ReaderConfig,
    *,
    selector: str = "all",
    unit: str | None = None,
) -> None:
    if not text.strip():
        raise ValueError("no text to read")
    effective_unit = unit or cfg.unit
    # A paragraph selector must use PyKokoro's paragraph descriptors so selection
    # and synthesis agree on what a paragraph is.
    prepare_unit = "paragraph" if selector != "all" else effective_unit
    with (
        _build_pipeline(cfg) as pipeline,
        pipeline.prepare_units(text, unit=prepare_unit) as prepared,
    ):
        indices = _selected_indices(prepared, selector)
        _play_rendered(prepared, indices=indices, cfg=cfg)


def speak_live(lines: Iterable[str], cfg: ReaderConfig, *, unit: str | None = None) -> None:
    """Read a live stdin stream paragraph-by-paragraph through one audio stream.

    Each paragraph is prepared independently because future stdin is not yet
    available. Within each paragraph, PyKokoro still renders sentence or paragraph
    units into one persistent sounddevice stream.
    """
    from pykokoro.playback import SoundDevicePlayer

    effective_unit = unit or cfg.unit
    player = None
    saw_text = False
    try:
        with _build_pipeline(cfg) as pipeline:
            for paragraph in iter_live_paragraphs(lines):
                saw_text = True
                with pipeline.prepare_units(paragraph, unit=effective_unit) as prepared:
                    for result in prepared.render():
                        try:
                            if player is None:
                                channels = (
                                    1 if result.audio.ndim == 1 else int(result.audio.shape[1])
                                )
                                player = SoundDevicePlayer(
                                    result.sample_rate,
                                    device=cfg.device,
                                    queue_size=cfg.queue_size,
                                    channels=channels,
                                ).start()
                            player.submit(result.audio)
                        finally:
                            result.release_audio()
            if player is not None:
                player.drain()
    finally:
        if player is not None:
            player.close()
    if not saw_text:
        raise ValueError("no text to read")
