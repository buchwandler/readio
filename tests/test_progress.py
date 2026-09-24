from __future__ import annotations

import io

from readio.api import ReadioEvent
from readio.audio import RenderSummary
from readio.progress import TerminalProgress, format_duration


class Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def render_event(completed: int, total: int | None, samples: int = 0) -> ReadioEvent:
    return ReadioEvent(
        kind="progress",
        operation="render",
        stage="synthesis",
        progress_kind="unit.completed",
        completed=completed,
        total=total,
        sample_count=samples,
        sample_rate=24000 if samples else None,
    )


def test_format_duration() -> None:
    assert format_duration(0) == "00:00"
    assert format_duration(9) == "00:09"
    assert format_duration(125) == "02:05"
    assert format_duration(3725) == "1:02:05"


def test_public_synthesis_progress_formats_eta_and_live_audio() -> None:
    stream = io.StringIO()
    clock = Clock()
    progress = TerminalProgress(stream=stream, enabled=True, tty=False, clock=clock)

    progress.public_event(render_event(0, 10))
    assert "ETA" not in stream.getvalue()
    clock.value = 100.5
    progress.public_event(render_event(1, 10))
    assert "ETA" not in stream.getvalue()
    clock.value = 120
    progress.public_event(render_event(2, 10))
    assert "ETA ~01:20" in stream.getvalue()

    live = io.StringIO()
    live_progress = TerminalProgress(stream=live, enabled=True, tty=False, clock=Clock())
    live_progress.public_event(render_event(7, None, 24000 * 79))
    output = live.getvalue()
    assert "Rendering live input" in output
    assert "7 units" in output
    assert "audio 01:19" in output
    assert "%" not in output
    assert "ETA" not in output


def test_public_composition_events_render_lifecycle_and_progress() -> None:
    stream = io.StringIO()
    clock = Clock()
    progress = TerminalProgress(stream=stream, enabled=True, tty=False, clock=clock)

    progress.public_event(
        ReadioEvent(
            kind="stage.started",
            operation="projects.compose",
            stage="composition",
            message="Preparing composition",
        )
    )
    progress.public_event(
        ReadioEvent(
            kind="progress",
            operation="projects.compose",
            stage="composition",
            progress_kind="phase",
            message="Composing audio",
            details={"clip_items": 2},
        )
    )
    progress.public_event(
        ReadioEvent(
            kind="progress",
            operation="projects.compose",
            stage="composition",
            progress_kind="segment.started",
            message="Preparing segment",
            completed=0,
            total=2,
            segment_id="segment-1",
        )
    )
    clock.value = 102
    progress.public_event(
        ReadioEvent(
            kind="progress",
            operation="projects.compose",
            stage="composition",
            progress_kind="segment.completed",
            message="Segment complete",
            completed=1,
            total=2,
            segment_id="segment-1",
            audio_seconds=5.0,
            total_audio_seconds=10.0,
        )
    )
    progress.public_event(
        ReadioEvent(
            kind="stage.completed",
            operation="projects.compose",
            stage="composition",
            sample_count=48000,
            sample_rate=24000,
            details={"items": 2, "frames": 48000},
        )
    )

    output = stream.getvalue()
    assert "Preparing composition…" in output
    assert "Composing audio…" in output
    assert "Composing  50%  1/2 segments" in output
    assert "Composition complete: 1 segments in 00:02" in output
    assert "audio 00:02" in output


def test_non_tty_progress_throttles_and_completion_uses_latest_unit() -> None:
    stream = io.StringIO()
    clock = Clock()
    progress = TerminalProgress(stream=stream, enabled=True, tty=False, clock=clock)

    progress.public_event(render_event(0, 100))
    progress.public_event(render_event(1, 100))
    progress.complete(RenderSummary())

    output = stream.getvalue()
    assert output.count("Rendering") == 1
    assert "Rendered 1 units" in output


def test_tty_progress_updates_in_place_and_completion_ends_line() -> None:
    stream = io.StringIO()
    clock = Clock()
    progress = TerminalProgress(stream=stream, enabled=True, tty=True, clock=clock)

    progress.public_event(render_event(0, 2))
    clock.value = 102
    progress.public_event(render_event(2, 2, 48000))
    progress.complete(RenderSummary(sample_rate=24000, sample_count=48000, channels=1))
    progress.close()

    output = stream.getvalue()
    assert "\r" in output
    assert "Rendering 100%" in output
    assert "Rendered 2 units in 00:02" in output
    assert output.endswith("\n")


def test_public_progress_disables_on_stream_failure() -> None:
    class BrokenStream:
        def write(self, text: str) -> int:
            raise BrokenPipeError

        def flush(self) -> None:
            raise AssertionError("flush should not be reached")

    progress = TerminalProgress(stream=BrokenStream(), enabled=True, tty=True, clock=Clock())
    progress.public_event(render_event(0, 1))

    assert not progress.enabled
    progress.close()
