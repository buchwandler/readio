from __future__ import annotations

import io

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
    progress.close()
    assert not progress.enabled
