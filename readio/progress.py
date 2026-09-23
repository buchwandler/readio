from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, TextIO

from audiocompose import CompositionProgress
from typing_extensions import Self

from .audio import RenderProgress, RenderSummary


def format_duration(seconds: float) -> str:
    """Format a duration as MM:SS or H:MM:SS."""
    total = max(0, round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _format_composition_operation(operation: Mapping[str, Any] | None) -> str:
    if not operation:
        return "operation"
    kind = operation.get("type", "operation")
    if kind == "gain":
        return f"gain {float(operation.get('db', 0.0)):+.1f} dB"
    if kind == "tempo":
        return f"tempo ×{float(operation.get('factor', 1.0)):.2f}"
    if kind == "pitch":
        return f"pitch {float(operation.get('semitones', 0.0)):+.1f} st"
    if kind == "fade_in":
        return f"fade-in {float(operation.get('seconds', 0.0)):.2f} s"
    if kind == "fade_out":
        return f"fade-out {float(operation.get('seconds', 0.0)):.2f} s"
    return f"operation {kind}"


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

    def synthesis_event(self, event: Any) -> None:
        """Render concise persistent-project synthesis lifecycle events."""
        if not self._enabled:
            return
        kind = getattr(event, "kind", "")
        details = getattr(event, "details", {}) or {}
        self._finish_line()
        if kind == "profile_resolved":
            target = details.get("target", {})
            if details.get("routing_mode") == "target":
                targets = details.get("targets", ())
                voice_bindings = details.get("voice_bindings", ())
                lines = [
                    f"Project: {details.get('project', '-')}\n",
                    f"Source:  {details.get('source', '-')} [{details.get('source_format', '-')}]\n",
                    f"Plan:    {details.get('plan_id', '-')} {details.get('selected_units', 0)} units\n",
                    "Synthesis\n",
                    f"  Engine:   {details.get('engine', '-')} {details.get('engine_version') or ''}\n",
                    f"  Provider: {details.get('provider', '-')}\n",
                    f"  Voices:   {len(targets)}\n",
                ]
                lines.extend(
                    f"    {item['role']:<12} {item['voice']}\n"
                    for item in voice_bindings
                )
                lines.append(f"  Profile:  {details.get('profile_id', '-')}\n")
                self._write("".join(lines), newline=True)
            else:
                self._write(
                    f"Project: {details.get('project', '-')}\n"
                    f"Source:  {details.get('source', '-')} [{details.get('source_format', '-')}]\n"
                    f"Plan:    {details.get('plan_id', '-')} {details.get('selected_units', 0)} units\n"
                    "Synthesis\n"
                    f"  Engine:   {details.get('engine', '-')} {details.get('engine_version') or ''}\n"
                    f"  Model:    {target.get('id', '-')}\n"
                    f"  Voice:    {target.get('voice', '-')}\n"
                    f"  Language: {target.get('language', '-')}\n"
                    f"  Profile:  {details.get('profile_id', '-')}\n",
                    newline=True,
                )
        elif kind == "cache_scanned":
            self._write(
                f"Cache: {details.get('reused', 0)} reusable, {details.get('rendered', 0)} to render",
                newline=True,
            )
        elif kind == "engine_open_started":
            target_id = details.get("target_id")
            label = f"Loading synthesis model {target_id}..." if target_id else "Loading synthesis model..."
            self._write(label, newline=True)
        elif kind == "unit_started":
            unit = getattr(event, "unit_id", "-")
            index = (getattr(event, "completed", 0) or 0) + 1
            total = getattr(event, "total", 0) or 0
            segment_ids = ",".join(details.get("segment_ids", ()))
            preview = getattr(event, "text", None) or ""
            self._write(
                f"[{index}/{total}] {unit} {segment_ids} {preview!r}",
                newline=True,
            )
        elif kind == "complete":
            self._write(
                f"Synthesis complete: {details.get('reused', 0)} reused, "
                f"{details.get('rendered', 0)} rendered",
                newline=True,
            )

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

    def _composition_elapsed(self, now: float | None = None) -> float:
        if self._composition_started_at is None:
            self._composition_started_at = self._clock() if now is None else now
        current = self._clock() if now is None else now
        return max(0.0, current - self._composition_started_at)

    def _composition_item_label(self, event: CompositionProgress) -> str:
        metadata = event.item_metadata or {}
        return str(metadata.get("segment_id") or event.item_id or "-")

    def _composition_is_silence(self, event: CompositionProgress) -> bool:
        return event.item_kind == "silence" or (event.item_metadata or {}).get("kind") == "silence"

    def _composition_step_label(self, event: CompositionProgress) -> str:
        if event.kind == "item_started":
            if self._composition_is_silence(event):
                seconds = (event.details or {}).get("seconds")
                if seconds is None:
                    frames = (event.item_metadata or {}).get("frames")
                    seconds = (
                        float(frames) / event.target_sample_rate
                        if frames and event.target_sample_rate
                        else 0.0
                    )
                seconds = float(seconds)
                return f"pause {seconds:.2f} s"
            return "preparing"
        if event.kind == "source_load_started":
            return "loading source"
        if event.kind == "operation_started":
            return _format_composition_operation(event.operation)
        if event.kind == "resample_started":
            return f"resampling {event.source_sample_rate} -> {event.target_sample_rate} Hz"
        if event.kind == "item_completed":
            return "complete"
        return self._composition_current_step

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

    def composition_event(self, event: CompositionProgress) -> None:
        """Render structured audiocompose events without sharing render timing state."""
        if not self._enabled:
            return
        now = self._clock()
        if event.kind == "compose_started":
            self._composition_started_at = now
            details = event.details or {}
            metadata_kinds = details.get("metadata_kinds", {})
            speech_count = (
                metadata_kinds.get("speech") if isinstance(metadata_kinds, dict) else None
            )
            self._composition_total_segments = int(
                speech_count if speech_count is not None else details.get("clip_items", 0)
            )
            self._composition_latest_completed = 0
            self._composition_current_segment = "-"
            self._composition_current_step = "preparing"
            self._composition_completed_audio_seconds = event.completed_audio_seconds
            self._composition_total_audio_seconds = event.total_audio_seconds
            self._composition_last_update_at = None
            self._composition_last_logged_percent = None
            self._composition_emit(event, now, force=True)
            return
        if event.completed_audio_seconds is not None:
            self._composition_completed_audio_seconds = event.completed_audio_seconds
        if event.total_audio_seconds is not None:
            self._composition_total_audio_seconds = event.total_audio_seconds
        if event.kind == "item_started":
            self._composition_current_segment = self._composition_item_label(event)
            self._composition_current_step = self._composition_step_label(event)
        elif event.kind == "item_completed":
            if event.item_kind == "clip" and not self._composition_is_silence(event):
                self._composition_latest_completed += 1
            self._composition_current_segment = self._composition_item_label(event)
            self._composition_current_step = self._composition_step_label(event)
        elif event.kind in {"source_load_started", "operation_started", "resample_started"}:
            self._composition_current_segment = self._composition_item_label(event)
            self._composition_current_step = self._composition_step_label(event)
        elif event.kind == "assembly_started":
            self.phase("Assembling master")
            return
        elif event.kind == "loudness_started":
            self.phase("Finalizing loudness and true peak")
            return
        elif event.kind == "compose_completed":
            self.composition_complete(event)
            return
        else:
            return
        self._composition_emit(event, now)

    def _composition_emit(
        self,
        event: CompositionProgress,
        now: float,
        *,
        force: bool = False,
    ) -> None:
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

    def composition_complete(self, event: CompositionProgress) -> None:
        if not self._enabled:
            return
        elapsed = self._composition_elapsed(self._clock())
        self._finish_line()
        text = (
            f"Composition complete: {self._composition_latest_completed} segments"
            f" in {format_duration(elapsed)}"
        )
        if event.target_sample_rate and event.output_frames is not None:
            text += f"  audio {format_duration(event.output_frames / event.target_sample_rate)}"
        self._write(text, newline=True)

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
