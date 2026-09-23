from __future__ import annotations

import io

from audiocompose import CompositionProgress

from readio.audio import RenderProgress, RenderSummary
from readio.progress import TerminalProgress, format_duration


class Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def event(completed: int, total: int | None, samples: int = 0) -> RenderProgress:
    return RenderProgress(completed, total, samples, 24000 if samples else 0)


def test_format_duration():
    assert format_duration(0) == "00:00"
    assert format_duration(9) == "00:09"
    assert format_duration(125) == "02:05"
    assert format_duration(3725) == "1:02:05"


def test_eta_requires_completed_unit_and_elapsed_time():
    stream = io.StringIO()
    clock = Clock()
    progress = TerminalProgress(stream=stream, enabled=True, tty=False, clock=clock)

    progress.update(event(0, 10))
    assert "ETA" not in stream.getvalue()

    clock.value = 100.5
    progress.update(event(1, 10))
    assert "ETA" not in stream.getvalue()

    clock.value = 120
    progress.update(event(2, 10))
    assert "ETA ~01:20" in stream.getvalue()


def test_live_progress_has_units_and_audio_but_no_percentage_or_eta():
    stream = io.StringIO()
    progress = TerminalProgress(stream=stream, enabled=True, tty=False, clock=Clock())

    progress.update(event(7, None, 24000 * 79))

    output = stream.getvalue()
    assert "Rendering live input" in output
    assert "7 units" in output
    assert "audio 01:19" in output
    assert "%" not in output
    assert "ETA" not in output


def test_tty_updates_in_place_and_complete_ends_line():
    stream = io.StringIO()
    clock = Clock()
    progress = TerminalProgress(stream=stream, enabled=True, tty=True, clock=clock)

    progress.update(event(0, 2))
    clock.value = 102
    progress.update(event(2, 2, 48000))
    progress.complete(RenderSummary(sample_rate=24000, sample_count=48000, channels=1))
    progress.close()

    output = stream.getvalue()
    assert "\r" in output
    assert "Rendering 100%" in output
    assert "Rendered 2 units in 00:02" in output
    assert output.endswith("\n")


def test_non_tty_updates_are_throttled_but_completion_uses_latest_unit():
    stream = io.StringIO()
    clock = Clock()
    progress = TerminalProgress(stream=stream, enabled=True, tty=False, clock=clock)

    progress.update(event(0, 100))
    progress.update(event(1, 100))
    progress.complete(RenderSummary())

    output = stream.getvalue()
    assert output.count("Rendering") == 1
    assert "Rendered 1 units" in output


def test_stream_failure_disables_progress_without_raising():
    class BrokenStream:
        def write(self, text: str) -> int:
            raise BrokenPipeError

        def flush(self) -> None:
            raise AssertionError("flush should not be reached")

    progress = TerminalProgress(stream=BrokenStream(), enabled=True, tty=True, clock=Clock())
    progress.update(event(0, 1))


def composition_event(
    kind: str,
    *,
    completed_items: int = 0,
    total_items: int = 2,
    item_index: int | None = None,
    item_id: str | None = None,
    item_kind: str | None = None,
    metadata: dict[str, object] | None = None,
    operation: dict[str, object] | None = None,
    source_rate: int | None = None,
    target_rate: int | None = 24000,
    completed_seconds: float | None = None,
    total_seconds: float | None = 10.0,
    details: dict[str, object] | None = None,
    output_frames: int | None = None,
) -> CompositionProgress:
    return CompositionProgress(
        kind=kind,
        completed_items=completed_items,
        total_items=total_items,
        item_index=item_index,
        item_id=item_id,
        item_kind=item_kind,
        item_metadata=metadata or {},
        operation=operation,
        source_sample_rate=source_rate,
        target_sample_rate=target_rate,
        completed_audio_seconds=completed_seconds,
        total_audio_seconds=total_seconds,
        output_frames=output_frames,
        details=details or {},
    )


def test_composition_progress_renders_steps_and_eta() -> None:
    stream = io.StringIO()
    clock = Clock()
    progress = TerminalProgress(stream=stream, enabled=True, tty=False, clock=clock)

    progress.composition_event(composition_event("compose_started", details={"clip_items": 2}))
    progress.composition_event(
        composition_event(
            "item_started",
            item_index=0,
            item_id="clip-1",
            item_kind="clip",
            metadata={"segment_id": "seg-0042"},
        )
    )
    clock.value = 101.0
    progress.composition_event(
        composition_event(
            "item_completed",
            completed_items=1,
            item_index=0,
            item_id="clip-1",
            item_kind="clip",
            metadata={"segment_id": "seg-0042"},
            completed_seconds=5.0,
        )
    )

    output = stream.getvalue()
    assert "0/2 segments" in output
    assert "1/2 segments" in output
    assert "seg-0042" in output
    assert "ETA ~00:01" in output


def test_composition_tty_shows_operation_resample_and_phases() -> None:
    stream = io.StringIO()
    progress = TerminalProgress(stream=stream, enabled=True, tty=True, clock=Clock())
    progress.composition_event(
        composition_event("compose_started", details={"clip_items": 1}, total_seconds=1.0)
    )
    progress.composition_event(
        composition_event(
            "operation_started",
            item_index=0,
            item_id="clip-1",
            item_kind="clip",
            metadata={"segment_id": "seg-1"},
            operation={"type": "tempo", "factor": 0.9},
        )
    )
    progress.composition_event(
        composition_event(
            "resample_started",
            item_index=0,
            item_id="clip-1",
            item_kind="clip",
            metadata={"segment_id": "seg-1"},
            source_rate=22050,
            target_rate=24000,
        )
    )
    progress.composition_event(composition_event("assembly_started", total_seconds=1.0))
    progress.composition_event(composition_event("loudness_started", total_seconds=1.0))
    progress.composition_event(
        composition_event(
            "compose_completed",
            total_seconds=1.0,
            output_frames=24000,
        )
    )

    output = stream.getvalue()
    assert "tempo ×0.90" in output
    assert "resampling 22050 -> 24000 Hz" in output
    assert "Assembling master…" in output
    assert "Finalizing loudness and true peak…" in output
    assert "Composition complete:" in output
    assert "\r" in output
    assert output.endswith("\n")


def test_composition_non_tty_throttles_updates_but_keeps_major_phases() -> None:
    stream = io.StringIO()
    clock = Clock()
    progress = TerminalProgress(stream=stream, enabled=True, tty=False, clock=clock)
    progress.composition_event(composition_event("compose_started", details={"clip_items": 10}))
    progress.composition_event(
        composition_event(
            "item_started",
            item_id="clip-1",
            item_kind="clip",
            metadata={"segment_id": "seg-1"},
        )
    )
    progress.composition_event(composition_event("assembly_started"))
    progress.composition_event(composition_event("loudness_started"))
    progress.close()

    output = stream.getvalue()
    assert output.count("Composing") == 1
    assert "Assembling master…" in output
    assert "Finalizing loudness and true peak…" in output


def test_composition_broken_stream_disables_progress() -> None:
    class BrokenStream:
        def write(self, text: str) -> int:
            raise BrokenPipeError

        def flush(self) -> None:
            raise AssertionError("flush should not be reached")

    progress = TerminalProgress(stream=BrokenStream(), enabled=True, tty=True, clock=Clock())
    progress.composition_event(composition_event("compose_started", details={"clip_items": 1}))
    assert not progress.enabled
    progress.close()
    assert not progress.enabled
