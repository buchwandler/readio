"""Resolved and bounded execution state for ReadioPlanV2."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

import numpy as np
from audiocompose import AudioJob, Composer, CompositionResult
from utterplan import UtterancePlan

from .audio import AudioSink, RenderProgress, RenderProgressCallback, RenderSummary
from .document import InputDocument
from .engines.base import EngineSelection
from .engines.registry import get_engine
from .errors import RenderError
from .plan import ReadioPlanV2
from .planning.compiler import CompiledSemanticPlan


@dataclass(frozen=True, slots=True)
class ResolvedExecutionV2:
    """Serializable v2 plan plus its in-memory execution artifacts."""

    plan: ReadioPlanV2

    @property
    def output(self) -> Any:
        return self.plan.output

    semantic: CompiledSemanticPlan | None
    document: InputDocument
    selection: EngineSelection | None

    @property
    def utterance_plan(self) -> UtterancePlan:
        """Return the semantic plan passed to the acoustic engine."""
        if self.semantic is None:
            raise RenderError("resolved v2 execution has no semantic plan")
        return self.semantic.plan


@dataclass(frozen=True, slots=True)
class BoundedRenderResult:
    """Result retaining all artifacts produced by a bounded render."""

    summary: RenderSummary
    audio_job: AudioJob
    composition: CompositionResult


def _artifact_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {"value": value}


def execute_bounded_v2(
    resolved: ResolvedExecutionV2,
    sink: AudioSink,
    *,
    on_progress: RenderProgressCallback | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> BoundedRenderResult:
    """Execute a resolved v2 plan without discovery or re-resolution."""
    plan = resolved.plan
    if not plan.ok or plan.render is None or resolved.selection is None:
        raise RenderError("cannot execute an unresolved v2 plan")
    if resolved.semantic is None:
        raise RenderError("cannot execute v2 plan without a semantic artifact")

    adapter = get_engine(plan.render.engine)
    with adapter.open(resolved.selection) as session:
        audio_job = session.to_audio_job(
            resolved.semantic.plan,
            options=resolved.selection.options,
        )

    if on_phase is not None and plan.output.format:
        on_phase(f"Composing {plan.output.format.upper()}")
    composition = Composer().compose(audio_job)
    audio = np.asarray(composition.audio)
    if audio.size == 0:
        raise RenderError("render produced no audio")

    total_units = len(audio_job.items)
    if on_progress is not None:
        on_progress(RenderProgress(0, total_units, 0, composition.sample_rate))
    sink.write(audio, composition.sample_rate)
    if on_progress is not None:
        on_progress(
            RenderProgress(
                total_units,
                total_units,
                int(audio.shape[0]),
                composition.sample_rate,
            )
        )

    markers = tuple(_artifact_dict(marker) for marker in composition.markers)
    summary = RenderSummary(
        sample_rate=composition.sample_rate,
        sample_count=int(audio.shape[0]),
        channels=1 if audio.ndim == 1 else int(audio.shape[1]),
        document_metadata={},
        markers=markers,
    )
    return BoundedRenderResult(
        summary=summary,
        audio_job=audio_job,
        composition=composition,
    )


__all__ = [
    "BoundedRenderResult",
    "ResolvedExecutionV2",
    "execute_bounded_v2",
]
