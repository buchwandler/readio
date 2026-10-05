from __future__ import annotations

import io

import pytest

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


def test_plan_progress_reports_typed_phases_and_scope_based_eta() -> None:
    stream = io.StringIO()
    clock = Clock()
    progress = TerminalProgress(stream=stream, enabled=True, tty=False, clock=clock)
    progress.public_event(
        ReadioEvent(kind="stage.started", operation="projects.plan", stage="plan")
    )
    progress.public_event(
        ReadioEvent(
            kind="progress",
            operation="projects.plan",
            stage="plan",
            progress_kind="item.started",
            scope_id="ch-0001",
            completed=0,
            total=2,
            details={"scope_index": 1},
        )
    )
    progress.public_event(
        ReadioEvent(
            kind="progress",
            operation="projects.plan",
            stage="plan",
            progress_kind="phase",
            message="Linguistic analysis",
            scope_id="ch-0001",
            details={
                "phase": "source_analysis",
                "event_kind": "phase.started",
                "pass_index": 1,
                "pass_total": 2,
                "language": "en-us",
                "provider": "spacy",
                "model": "en_core_web_sm",
            },
        )
    )
    progress.public_event(
        ReadioEvent(
            kind="progress",
            operation="projects.plan",
            stage="plan",
            progress_kind="phase",
            message="Reusing linguistic analysis",
            scope_id="ch-0001",
            details={
                "phase": "spoken_analysis",
                "event_kind": "phase.started",
                "pass_index": 2,
                "pass_total": 2,
                "reused": True,
            },
        )
    )
    before_completion = stream.getvalue()
    assert (
        "Planning ch-0001  1/2  Linguistic analysis 1/2 · en-us · spaCy · en_core_web_sm…"
        in before_completion
    )
    assert "Reusing linguistic analysis…" in before_completion
    assert "%" not in before_completion

    clock.value = 102
    progress.public_event(
        ReadioEvent(
            kind="progress",
            operation="projects.plan",
            stage="plan",
            progress_kind="item.completed",
            scope_id="ch-0001",
            completed=1,
            total=2,
            details={"scope_index": 1},
        )
    )
    assert "Planning  50%  1/2 scopes  elapsed 00:02  ETA ~00:02" in stream.getvalue()

    clock.value = 104
    progress.public_event(
        ReadioEvent(
            kind="progress",
            operation="projects.plan",
            stage="plan",
            progress_kind="item.completed",
            scope_id="ch-0002",
            completed=2,
            total=2,
            details={"scope_index": 2},
        )
    )
    assert "Planning 100%  2/2 scopes  elapsed 00:04" in stream.getvalue()
    assert "ETA" not in stream.getvalue().splitlines()[-1]


@pytest.mark.parametrize("tty", [False, True])
def test_synthesis_progress_shows_full_text_chapter_transitions_and_segment_counts(
    tty: bool,
) -> None:
    stream = io.StringIO()
    progress = TerminalProgress(stream=stream, enabled=True, tty=tty, clock=Clock())
    long_text = (
        "Synthesis progress must keep every renderer character and exact whitespace visible.\n\t"
        * 3
    ) + "FULL_TEXT_TAIL_SENTINEL"

    def started(scope_id: str, segment_id: str, completed: int, details: dict) -> ReadioEvent:
        return ReadioEvent(
            kind="progress",
            operation="projects.synthesize",
            stage="synthesis",
            progress_kind="segment.started",
            completed=completed,
            total=210,
            scope_id=scope_id,
            unit_id="unit-0052",
            segment_id=segment_id,
            details=details,
        )

    first_details = {
        "text": long_text,
        "scope_kind": "chapter",
        "scope_title": "The Long Night",
        "scope_number": 17,
        "scope_index": 2,
        "scope_total": 3,
    }
    progress.public_event(started("chapter-0017", "seg-000057", 56, first_details))
    progress.public_event(
        started(
            "chapter-0017",
            "seg-000058",
            57,
            {**first_details, "text": "The next complete segment."},
        )
    )
    progress.public_event(
        started(
            "chapter-0018",
            "seg-000059",
            58,
            {
                "text": "Another chapter.",
                "scope_kind": "chapter",
                "scope_title": "The Return",
                "scope_number": 3,
                "scope_index": 3,
                "scope_total": 3,
            },
        )
    )
    progress.public_event(
        ReadioEvent(
            kind="progress",
            operation="projects.synthesize",
            stage="synthesis",
            progress_kind="segment.completed",
            completed=57,
            total=210,
        )
    )

    output = stream.getvalue()
    first_heading = "Chapter 2/3 selected · source chapter 17 · The Long Night · chapter-0017"
    second_heading = "Chapter 3/3 · The Return · chapter-0018"
    assert output.count(first_heading) == 1
    assert output.count(second_heading) == 1
    assert repr(long_text) in output
    assert "FULL_TEXT_TAIL_SENTINEL" in output
    assert "[57/210 segments] unit-0052 · seg-000057" in output
    assert "[58/210 segments] unit-0052 · seg-000058" in output
    assert "[59/210 segments] unit-0052 · seg-000059" in output
    assert "57/210 segments" in output
    assert "57/210 units" not in output


def test_single_document_synthesis_does_not_print_redundant_scope_heading() -> None:
    stream = io.StringIO()
    progress = TerminalProgress(stream=stream, enabled=True, tty=False, clock=Clock())
    progress.public_event(
        ReadioEvent(
            kind="progress",
            operation="projects.synthesize",
            stage="synthesis",
            progress_kind="segment.started",
            completed=0,
            total=1,
            scope_id="document",
            unit_id="unit-0001",
            segment_id="seg-000001",
            details={"scope_kind": "document", "text": "A sentence."},
        )
    )

    output = stream.getvalue()
    assert "Document ·" not in output
    assert "[1/1 segments] unit-0001 · seg-000001 'A sentence.'" in output
