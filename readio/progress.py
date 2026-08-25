from __future__ import annotations

import time
from collections.abc import Callable
from typing import Self, TextIO

from .audio import RenderProgress, RenderSummary


def format_duration(seconds: float) -> str:
    """Format a duration as MM:SS or H:MM:SS."""
    total = max(0, round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


class TerminalProgress:
    """Render low-noise progress updates to a terminal or log stream."""

    def __init__(
        self,
        *,
        stream: TextIO,
        enabled: bool,
        tty: bool,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stream = stream
        self._enabled = enabled
        self._tty = tty
        self._clock = clock
        self._started_at: float | None = None
        self._last_update_at: float | None = None
        self._last_logged_percent: int | None = None
        self._last_logged_completed = -1
        self._latest_completed = 0
        self._previous_width = 0
        self._line_active = False
        self._closed = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def _write(self, text: str, *, newline: bool = False, inplace: bool = False) -> None:
        if not self._enabled:
            return
        try:
            if inplace:
                padding = max(0, self._previous_width - len(text))
                self._stream.write("\r" + text + (" " * padding))
                self._previous_width = len(text)
                self._line_active = True
            else:
                self._stream.write(text + ("\n" if newline else ""))
                self._previous_width = 0
                self._line_active = False
            self._stream.flush()
        except (BrokenPipeError, OSError):
            self._enabled = False
            self._line_active = False

    def _finish_line(self) -> None:
        if self._enabled and self._line_active:
            self._write("", newline=True)

    def _elapsed(self, now: float | None = None) -> float:
        if self._started_at is None:
            self._started_at = self._clock() if now is None else now
        current = self._clock() if now is None else now
        return max(0.0, current - self._started_at)

    def phase(self, name: str, detail: str | None = None) -> None:
        if not self._enabled:
            return
        self._finish_line()
        text = name if detail is None else f"{name} {detail}"
        self._write(text + "…", newline=True)

    def render_started(self) -> None:
        if self._started_at is None:
            self._started_at = self._clock()

    def _should_emit(self, event: RenderProgress, now: float) -> bool:
        if self._tty:
            return True
        if self._last_update_at is None:
            return True
        if now - self._last_update_at >= 30.0:
            return True
        if event.total_units is not None:
            percent = (
                min(100, round(event.completed_units * 100 / event.total_units))
                if event.total_units
                else 100
            )
            return self._last_logged_percent is None or percent >= self._last_logged_percent + 10
        return False

    def _render_text(self, event: RenderProgress, elapsed: float) -> str:
        if event.total_units is None:
            text = f"Rendering live input  {event.completed_units} units  elapsed {format_duration(elapsed)}"
        else:
            total = max(0, event.total_units)
            completed = min(event.completed_units, total) if total else 0
            percent = 100 if total == 0 else min(100, round(completed * 100 / total))
            text = (
                f"Rendering {percent:3d}%  {completed}/{total} units"
                f"  elapsed {format_duration(elapsed)}"
            )
            if completed > 0 and completed < total and elapsed >= 1.0:
                eta = elapsed / completed * (total - completed)
                if eta > 0:
                    text += f"  ETA ~{format_duration(eta)}"
        if event.sample_rate > 0:
            text += f"  audio {format_duration(event.sample_count / event.sample_rate)}"
        return text

    def update(self, event: RenderProgress) -> None:
        if not self._enabled:
            return
        self._latest_completed = event.completed_units
        now = self._clock()
        if self._started_at is None:
            self._started_at = now
        if not self._should_emit(event, now):
            return
        elapsed = self._elapsed(now)
        text = self._render_text(event, elapsed)
        if self._tty:
            self._write(text, inplace=True)
        else:
            self._write(text, newline=True)
        self._last_update_at = now
        self._last_logged_completed = event.completed_units
        if event.total_units is not None:
            total = max(0, event.total_units)
            self._last_logged_percent = (
                100 if total == 0 else min(100, round(event.completed_units * 100 / total))
            )

    def complete(self, summary: RenderSummary) -> None:
        if not self._enabled:
            return
        now = self._clock()
        if self._started_at is None:
            self._started_at = now
        self._finish_line()
        elapsed = self._elapsed(now)
        text = f"Rendered {self._latest_completed} units in {format_duration(elapsed)}"
        if summary.sample_rate > 0:
            text += f"  audio {format_duration(summary.sample_count / summary.sample_rate)}"
        self._write(text, newline=True)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._finish_line()
