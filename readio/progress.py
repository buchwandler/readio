from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import TextIO

from typing_extensions import Self

from .api.events import ReadioEvent
from .audio import RenderSummary


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
        self._plan_started_at: float | None = None
        self._plan_total_scopes: int | None = None
        self._plan_completed_scopes = 0
        self._plan_current_scope = "document"
        self._plan_current_scope_index = 1
        self._plan_current_phase: str | None = None
        self._plan_last_logged_percent: int | None = None
        self._plan_last_update_at: float | None = None
        self._last_update_at: float | None = None
        self._last_logged_percent: int | None = None
        self._last_logged_completed = -1
        self._latest_completed = 0
        self._synthesis_scope_id: str | None = None
        self._composition_started_at: float | None = None
        self._composition_last_update_at: float | None = None
        self._composition_last_logged_percent: int | None = None
        self._composition_latest_completed = 0
        self._composition_total_segments = 0
        self._composition_current_segment = "-"
        self._composition_current_step = "preparing"
        self._composition_completed_audio_seconds: float | None = None
        self._composition_total_audio_seconds: float | None = None
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

    def _should_emit_values(
        self, completed_units: int, total_units: int | None, now: float
    ) -> bool:
        if self._tty:
            return True
        if self._last_update_at is None:
            return True
        if now - self._last_update_at >= 30.0:
            return True
        if total_units is not None:
            percent = min(100, round(completed_units * 100 / total_units)) if total_units else 100
            return self._last_logged_percent is None or percent >= self._last_logged_percent + 10
        return False

    def _render_text_values(
        self,
        completed_units: int,
        total_units: int | None,
        sample_count: int,
        sample_rate: int,
        elapsed: float,
        *,
        noun: str = "units",
    ) -> str:
        if total_units is None:
            text = f"Rendering live input  {completed_units} {noun}  elapsed {format_duration(elapsed)}"
        else:
            total = max(0, total_units)
            completed = min(completed_units, total) if total else 0
            percent = 100 if total == 0 else min(100, round(completed * 100 / total))
            text = (
                f"Rendering {percent:3d}%  {completed}/{total} {noun}"
                f"  elapsed {format_duration(elapsed)}"
            )
            if completed > 0 and completed < total and elapsed >= 1.0:
                eta = elapsed / completed * (total - completed)
                if eta > 0:
                    text += f"  ETA ~{format_duration(eta)}"
        if sample_rate > 0:
            text += f"  audio {format_duration(sample_count / sample_rate)}"
        return text

    def _update_values(
        self,
        completed_units: int,
        total_units: int | None,
        sample_count: int,
        sample_rate: int,
        now: float,
        *,
        noun: str = "units",
    ) -> None:
        if not self._enabled:
            return
        self._latest_completed = completed_units
        if self._started_at is None:
            self._started_at = now
        if not self._should_emit_values(completed_units, total_units, now):
            return
        elapsed = self._elapsed(now)
        text = self._render_text_values(
            completed_units,
            total_units,
            sample_count,
            sample_rate,
            elapsed,
            noun=noun,
        )
        if self._tty:
            self._write(text, inplace=True)
        else:
            self._write(text, newline=True)
        self._last_update_at = now
        self._last_logged_completed = completed_units
        if total_units is not None:
            total = max(0, total_units)
            self._last_logged_percent = (
                100 if total == 0 else min(100, round(completed_units * 100 / total))
            )

    def public_event(self, event: ReadioEvent) -> None:
        """Render a stable public API event without reconstructing internal callbacks."""
        if not self._enabled:
            return
        if event.kind == "stage.started":
            if event.stage == "plan":
                self._plan_started_at = self._clock()
                self._plan_total_scopes = None
                self._plan_completed_scopes = 0
                self._plan_current_scope = "document"
                self._plan_current_scope_index = 1
                self._plan_current_phase = None
                self._plan_last_logged_percent = None
                self._plan_last_update_at = None
                self.phase(event.message or "Planning")
                return
            if event.stage == "synthesis":
                self._synthesis_scope_id = None
                self._started_at = self._clock()
                self._last_update_at = None
                self._last_logged_percent = None
                self._latest_completed = 0
            if event.stage == "composition":
                self._composition_started_at = self._clock()
                self._composition_last_update_at = None
                self._composition_last_logged_percent = None
                self._composition_latest_completed = 0
                self._composition_total_segments = 0
                self._composition_current_segment = "-"
                self._composition_current_step = "preparing"
            label = event.message or (event.stage or "Working").replace("_", " ").title()
            self.phase(label)
            return
        if event.kind == "stage.completed":
            if event.stage == "composition":
                self._composition_public_complete(event)
            elif event.stage == "synthesis":
                details = event.details
                reused = details.get("reused", 0)
                rendered = details.get("rendered", 0)
                self._finish_line()
                self._write(
                    f"Synthesis complete: {reused} reused, {rendered} rendered",
                    newline=True,
                )
            return
        if event.kind != "progress":
            return
        if event.stage == "plan":
            self._plan_public_event(event)
        elif event.stage == "synthesis":
            self._synthesis_public_event(event)
        elif event.stage == "composition":
            self._composition_public_event(event)

    def _plan_public_event(self, event: ReadioEvent) -> None:
        details = event.details
        if event.progress_kind == "item.started":
            self._plan_current_scope = event.scope_id or "document"
            scope_index = details.get("scope_index")
            if isinstance(scope_index, int) and not isinstance(scope_index, bool):
                self._plan_current_scope_index = scope_index
            if event.total is not None:
                self._plan_total_scopes = event.total
            self._plan_current_phase = "preparation"
            self._plan_emit_phase("Preparing")
            return
        if event.progress_kind == "item.completed":
            if event.scope_id is not None:
                self._plan_current_scope = event.scope_id
            if event.completed is not None:
                self._plan_completed_scopes = event.completed
            else:
                self._plan_completed_scopes += 1
            if event.total is not None:
                self._plan_total_scopes = event.total
            self._plan_emit_summary()
            return
        if event.progress_kind != "phase":
            return

        event_kind = details.get("event_kind")
        if event_kind in {"phase.completed", "run.completed", "model.completed"}:
            return
        if event.scope_id is not None:
            self._plan_current_scope = event.scope_id
        phase = details.get("phase")
        self._plan_current_phase = phase if isinstance(phase, str) else None
        message = event.message or "Planning"
        if message == "Linguistic analysis":
            pass_index = details.get("pass_index")
            pass_total = details.get("pass_total")
            if isinstance(pass_index, int) and isinstance(pass_total, int):
                message += f" {pass_index}/{pass_total}"
        suffix: list[str] = []
        if message.startswith("Loading "):
            model = details.get("model")
            if isinstance(model, str) and model:
                suffix.append(model)
        elif message.startswith("Linguistic analysis"):
            language = details.get("language")
            provider = details.get("provider")
            model = details.get("model")
            if isinstance(language, str) and language:
                suffix.append(language)
            if isinstance(provider, str) and provider:
                suffix.append("spaCy" if provider == "spacy" else provider)
            if isinstance(model, str) and model:
                suffix.append(model)
        if suffix:
            message += (" " if message.startswith("Loading ") else " · ") + " · ".join(suffix)
        self._plan_emit_phase(message)

    def _plan_prefix(self) -> str:
        scope = self._plan_current_scope or "document"
        total = self._plan_total_scopes
        if total is None:
            return f"Planning {scope}"
        return f"Planning {scope}  {self._plan_current_scope_index}/{total}"

    def _plan_emit_phase(self, message: str) -> None:
        self._finish_line()
        self._write(f"{self._plan_prefix()}  {message}…", newline=True)
        self._plan_last_update_at = self._clock()

    def _plan_emit_summary(self) -> None:
        total = self._plan_total_scopes
        if total is None:
            return
        completed = min(self._plan_completed_scopes, total) if total else 0
        percent = 100 if total == 0 else min(100, round(completed * 100 / total))
        now = self._clock()
        started_at = self._plan_started_at if self._plan_started_at is not None else now
        elapsed = max(0.0, now - started_at)
        noun = "scope" if total == 1 else "scopes"
        text = (
            f"Planning {percent:3d}%  {completed}/{total} {noun}"
            f"  elapsed {format_duration(elapsed)}"
        )
        if completed > 0 and total > completed and elapsed >= 1.0:
            eta = elapsed / completed * (total - completed)
            if eta > 0:
                text += f"  ETA ~{format_duration(eta)}"
        self._finish_line()
        self._write(text, newline=True)
        self._plan_last_logged_percent = percent
        self._plan_last_update_at = now

    def _synthesis_scope_heading(self, event: ReadioEvent) -> str | None:
        scope_id = event.scope_id
        if scope_id is None:
            return None
        details = event.details
        scope_kind = details.get("scope_kind")
        scope_title = details.get("scope_title")
        if scope_kind == "chapter":
            parts: list[str] = []
            scope_index = details.get("scope_index")
            scope_total = details.get("scope_total")
            scope_number = details.get("scope_number")
            if (
                isinstance(scope_index, int)
                and not isinstance(scope_index, bool)
                and isinstance(scope_total, int)
                and not isinstance(scope_total, bool)
            ):
                chapter = f"Chapter {scope_index}/{scope_total}"
                if (
                    isinstance(scope_number, int)
                    and not isinstance(scope_number, bool)
                    and scope_number != scope_index
                ):
                    chapter += " selected"
                    parts.append(f"source chapter {scope_number}")
                parts.insert(0, chapter)
            elif isinstance(scope_number, int) and not isinstance(scope_number, bool):
                parts.append(f"source chapter {scope_number}")
            if isinstance(scope_title, str) and scope_title:
                parts.append(scope_title)
            parts.append(scope_id)
            return " · ".join(parts)
        if scope_kind == "document":
            return (
                f"Document · {scope_title}"
                if isinstance(scope_title, str) and scope_title
                else None
            )
        if isinstance(scope_title, str) and scope_title:
            label = scope_kind.title() if isinstance(scope_kind, str) else "Scope"
            return f"{label} · {scope_title} · {scope_id}"
        return None

    def _synthesis_public_event(self, event: ReadioEvent) -> None:
        if event.progress_kind == "phase":
            if event.message:
                self._finish_line()
                self._write(event.message, newline=True)
        elif event.progress_kind in {"unit.started", "segment.started"}:
            if event.scope_id != self._synthesis_scope_id:
                self._finish_line()
                if (heading := self._synthesis_scope_heading(event)) is not None:
                    self._write(heading, newline=True)
                self._synthesis_scope_id = event.scope_id

            details = event.details
            raw_text = details.get("text", "")
            segment_text = raw_text if isinstance(raw_text, str) else ""
            is_segment = event.progress_kind == "segment.started"
            noun = "segments" if is_segment else "units"
            index = (event.completed or 0) + 1
            total = event.total
            counter = f"[{index}/{total} {noun}]" if total is not None else f"[{index} {noun}]"
            labels = [event.unit_id or "-"]
            if event.segment_id is not None:
                labels.append(event.segment_id)
            else:
                segment_ids = details.get("segment_ids", ())
                if isinstance(segment_ids, (tuple, list)) and segment_ids:
                    labels.extend(str(item) for item in segment_ids)
            self._finish_line()
            self._write(
                f"{counter} {' · '.join(labels)} {segment_text!r}",
                newline=True,
            )
        elif event.progress_kind in {"unit.completed", "segment.completed"}:
            self._update_values(
                event.completed or 0,
                event.total,
                event.sample_count or 0,
                event.sample_rate or 0,
                self._clock(),
                noun="segments" if event.progress_kind == "segment.completed" else "units",
            )

    def _composition_public_event(self, event: ReadioEvent) -> None:
        details = event.details
        if event.progress_kind == "phase":
            metadata_kinds = details.get("metadata_kinds", {})
            if isinstance(metadata_kinds, Mapping):
                speech_count = metadata_kinds.get("speech")
            else:
                speech_count = None
            total = event.total if event.total is not None else speech_count
            if total is None:
                total = details.get("clip_items")
            if isinstance(total, int):
                self._composition_total_segments = total
            if event.message:
                self.phase(event.message)
            return
        if event.progress_kind not in {
            "item.started",
            "item.completed",
            "segment.started",
            "segment.completed",
        }:
            return
        now = self._clock()
        if self._composition_started_at is None:
            self._composition_started_at = now
        if event.audio_seconds is not None:
            self._composition_completed_audio_seconds = event.audio_seconds
        if event.total_audio_seconds is not None:
            self._composition_total_audio_seconds = event.total_audio_seconds
        self._composition_current_segment = event.segment_id or "-"
        self._composition_current_step = event.message or "preparing"
        if event.completed is not None:
            self._composition_latest_completed = event.completed
        elif event.progress_kind == "segment.completed":
            self._composition_latest_completed += 1
        if event.total is not None:
            self._composition_total_segments = event.total
        if self._composition_should_emit(now):
            self._composition_emit_values(now)

    def _composition_public_complete(self, event: ReadioEvent) -> None:
        details = event.details
        frames = event.sample_count if event.sample_count is not None else details.get("frames")
        sample_rate = (
            event.sample_rate if event.sample_rate is not None else details.get("sample_rate")
        )
        if self._composition_started_at is None:
            self._composition_started_at = self._clock()
        elapsed = self._composition_elapsed(self._clock())
        self._finish_line()
        text = (
            f"Composition complete: {self._composition_latest_completed} segments"
            f" in {format_duration(elapsed)}"
        )
        if isinstance(frames, int) and isinstance(sample_rate, int) and sample_rate:
            text += f"  audio {format_duration(frames / sample_rate)}"
        self._write(text, newline=True)

    def _composition_elapsed(self, now: float | None = None) -> float:
        if self._composition_started_at is None:
            self._composition_started_at = self._clock() if now is None else now
        current = self._clock() if now is None else now
        return max(0.0, current - self._composition_started_at)

    def _composition_should_emit(self, now: float) -> bool:
        if self._tty:
            return True
        if self._composition_last_update_at is None:
            return True
        if now - self._composition_last_update_at >= 30.0:
            return True
        percent = (
            min(
                100,
                round(self._composition_latest_completed * 100 / self._composition_total_segments),
            )
            if self._composition_total_segments
            else 100
        )
        return (
            self._composition_last_logged_percent is None
            or percent >= self._composition_last_logged_percent + 10
        )

    def _composition_eta(self, elapsed: float) -> float | None:
        completed = self._composition_latest_completed
        total = self._composition_total_segments
        if completed <= 0 or elapsed < 1.0 or completed >= total:
            return None
        completed_seconds = self._composition_completed_audio_seconds
        total_seconds = self._composition_total_audio_seconds
        if completed_seconds is not None and total_seconds is not None and completed_seconds > 0:
            remaining = total_seconds - completed_seconds
            if remaining > 0:
                return elapsed / completed_seconds * remaining
        return elapsed / completed * (total - completed)

    def _composition_text(self, elapsed: float) -> str:
        total = self._composition_total_segments
        completed = min(self._composition_latest_completed, total) if total else 0
        percent = 100 if total == 0 else min(100, round(completed * 100 / total))
        text = (
            f"Composing {percent:3d}%  {completed}/{total} segments"
            f"  elapsed {format_duration(elapsed)}"
        )
        if (eta := self._composition_eta(elapsed)) is not None:
            text += f"  ETA ~{format_duration(eta)}"
        if self._composition_current_segment != "-":
            text += f"  {self._composition_current_segment}"
        if self._composition_current_step:
            text += f"  {self._composition_current_step}"
        return text

    def _composition_emit_values(self, now: float, *, force: bool = False) -> None:
        if not force and not self._composition_should_emit(now):
            return
        text = self._composition_text(self._composition_elapsed(now))
        if self._tty:
            self._write(text, inplace=True)
        else:
            self._write(text, newline=True)
        self._composition_last_update_at = now
        total = self._composition_total_segments
        self._composition_last_logged_percent = (
            100 if total == 0 else min(100, round(self._composition_latest_completed * 100 / total))
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
