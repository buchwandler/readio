"""Resolved and bounded execution state for ReadioPlanV2."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict, dataclass, is_dataclass, replace
from json import dumps
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
        """Return the compiled semantic plan lowered into Readio requests."""
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


def _selection_for_target(base: EngineSelection, target: Any) -> EngineSelection:
    metadata = dict(target.metadata)
    voice = base.voice if target.id == base.target_id else None
    if target.voice is not None:
        voice = target.voice.value if target.voice.kind == "named" else None
        if target.voice.kind == "reference":
            metadata["voice_source"] = target.voice.to_dict()
    return replace(
        base,
        target_id=target.id,
        language=target.language,
        voice=voice,
        speaker=target.speaker,
        options={**dict(base.options), **dict(target.options)},
        metadata=metadata,
    )


def _selection_key(selection: EngineSelection) -> str:
    return dumps(
        {
            "engine": selection.engine,
            "target_id": selection.target_id,
            "language": selection.language,
            "voice": selection.voice,
            "speaker": selection.speaker,
            "options": dict(selection.options),
            "metadata": dict(selection.metadata),
            "offline": selection.offline,
            "refresh": selection.refresh,
        },
        sort_keys=True,
        default=str,
    )


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
    capabilities = adapter.capabilities()
    default_target = plan.render.default_target
    if default_target is None:
        raise RenderError("resolved v2 plan has no default render target")
    role_targets = {binding.role: binding.target for binding in plan.render.role_bindings}
    from .rendering import lower_segment
    from .rendering.lowering import LoweringError
    from .stages.composition import _build_layout, _markers_by_segment

    rendered_segments: list[tuple[Any, dict[str, Any]]] = []
    markers_by_segment = _markers_by_segment(resolved.semantic.plan)
    sessions: dict[str, Any] = {}
    with ExitStack() as stack:
        for segment in resolved.semantic.plan.segments:
            voice_directive = segment.directives.voice
            reference = getattr(voice_directive, "reference", None)
            target = role_targets.get(reference, default_target) if reference else default_target
            selection = _selection_for_target(resolved.selection, target)
            try:
                lowered = lower_segment(
                    resolved.semantic.plan,
                    segment,
                    selection,
                    capabilities,
                )
            except LoweringError as error:
                raise RenderError(str(error)) from error
            key = _selection_key(selection)
            session = sessions.get(key)
            if session is None:
                session = stack.enter_context(adapter.open(selection))
                sessions[key] = session
            rendered = session.synthesize(lowered.request)
            if rendered.id != lowered.request.id:
                raise RenderError(
                    f"engine returned request {rendered.id!r}; expected {lowered.request.id!r}"
                )
            audio = np.asarray(rendered.audio, dtype=np.float32)
            speech_hash = hashlib.sha256(
                f"{resolved.semantic.sha256}:{segment.id}:{segment.text}".encode()
            ).hexdigest()
            rendered_segments.append(
                (
                    segment,
                    {
                        "scope_id": "document",
                        "cache_path": None,
                        "audio": audio,
                        "audio_sha256": hashlib.sha256(audio.tobytes()).hexdigest(),
                        "sample_rate": int(rendered.sample_rate),
                        "channels": 1,
                        "frames": len(audio),
                        "speech_hash": speech_hash,
                        "synthesis_key": plan.render.render_id or "",
                        "word_timings": tuple(rendered.word_timings),
                        "markers": markers_by_segment.get(str(segment.id), ()),
                        "composition": {
                            "rate": plan.render.rate,
                            **{
                                key: value
                                for key, value in plan.render.options.items()
                                if key in {"speed", "rate", "pitch", "volume", "emphasis"}
                            },
                        },
                        "voice_identity": f"{selection.target_id}:{selection.voice or ''}",
                        "prosody_transitions": (
                            resolved.semantic.plan.document_metadata.get("prosody_transitions")
                        ),
                        "engine_metadata": dict(rendered.metadata),
                        "warnings": tuple(rendered.warnings),
                        "lowering_diagnostics": tuple(lowered.diagnostics),
                        "plan_unit_id": lowered.unit_id,
                    },
                )
            )

    audio_job, _composition_identity = _build_layout(
        None,
        rendered_segments,
        target_lufs=plan.composition.target_lufs,
        true_peak_ceiling_dbtp=plan.composition.true_peak_ceiling_dbtp,
        peak_policy=plan.composition.peak_policy,
        clip_policy=plan.composition.clip_policy,
        output_sample_rate=plan.composition.sample_rate,
    )
    if on_phase is not None and plan.output.format:
        on_phase(f"Composing {plan.output.format.upper()}")
    composition = Composer().compose(audio_job)
    audio = np.asarray(composition.audio)
    if audio.size == 0:
        raise RenderError("render produced no audio")

    total_units = len(resolved.semantic.plan.segments)
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
