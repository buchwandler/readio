"""Request and result types for native workspace orchestration."""

from __future__ import annotations

import wave
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from io import BytesIO
from pathlib import Path
from threading import Event
from types import MappingProxyType
from typing import Any, Literal

import numpy as np

from readio.errors import OutputCollisionError, WorkflowCancelledError

from readio.workspace import (
    CreateWorkspaceRequest,
    PreparationOptions,
    WorkspaceMode,
    WorkspaceRef,
)

DiagnosticsMode = Literal["errors", "warnings", "all", "none"]
PipelineStage = Literal["convert", "compile", "render_plan", "render", "compose", "export"]


StatusState = Literal[
    "not_started",
    "partial",
    "complete",
    "stale",
    "blocked",
    "unavailable",
    "unknown",
    "error",
]
StatusStage = Literal["bundle", "compile", "render_plan", "render", "compose", "export"]


@dataclass(frozen=True, slots=True)
class StatusRequest:
    chapters: str = "all"
    profile: Path | None = None
    variant: str | None = None


@dataclass(frozen=True, slots=True)
class StageStatus:
    stage: StatusStage
    state: StatusState
    summary: str
    current: int | None = None
    total: int | None = None
    package: str | None = None
    details: Mapping[str, object] = dataclass_field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VariantStatus:
    variant: str
    profile: Path | None
    render_plan: StageStatus
    render: StageStatus
    compose: StageStatus
    export: StageStatus


@dataclass(frozen=True, slots=True)
class NextAction:
    stage: StatusStage
    reason: str
    argv: tuple[str, ...]
    destructive: bool = False
    requires: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BundleStatus:
    workspace: WorkspaceRef
    compile: StageStatus
    variants: tuple[VariantStatus, ...]
    next_actions: tuple[NextAction, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlanRequest:
    chapters: str = "all"
    language: str | None = None
    planner_config: Any | None = None
    diagnostics: DiagnosticsMode = "errors"
    force: bool = False


@dataclass(frozen=True, slots=True)
class VoiceRenderRequest:
    variant: str
    chapters: str = "all"
    speech: str = "all"
    renderplan_root: Path | None = None
    output_root: Path | None = None
    cache_dir: Path | None = None
    force: bool = False
    reset_stale_output: bool = False
    fail_fast: bool = False
    emphasis_policy: str = "reject"


@dataclass(frozen=True, slots=True)
class RenderPlanRequest:
    profile: Path
    variant: str | None = None
    chapters: str = "all"
    utterplan_root: Path | None = None
    output_root: Path | None = None
    force: bool = False
    pause_timing: PauseTiming = "fixed"

    def __post_init__(self) -> None:
        _validate_pause_timing(self.pause_timing)


@dataclass(frozen=True, slots=True)
class RenderPlanResult:
    native: Any
    variant: str


@dataclass(frozen=True, slots=True)
class ComposeRequest:
    variant: str
    chapters: str = "all"
    pause_policy: Any | None = None
    output_policy: Any | None = None
    options: Any | None = None
    renderplan_root: Path | None = None
    voicerender_root: Path | None = None
    output_root: Path | None = None
    media_root: Path | None = None
    save_audiojobs: Path | None = None
    force: bool = False
    fail_fast: bool = False
    emphasis_policy: str = "segment_fallback"


@dataclass(frozen=True, slots=True)
class ExportRequest:
    variant: str
    chapters: str = "all"
    output: Path | None = None
    format: str | None = None
    profile: Path | None = None
    bitrate: str | int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    metadata: Mapping[str, str] | None = None
    cover: Path | None = None
    timeline: Path | None = None
    audiocompose_root: Path | None = None
    output_root: Path | None = None
    force: bool = False
    ffmpeg: Path | None = None
    ffprobe: Path | None = None


@dataclass(frozen=True, slots=True)
class RunRequest:
    render_plan: RenderPlanRequest
    render: VoiceRenderRequest
    compose: ComposeRequest
    export: ExportRequest | None = None
    plan: PlanRequest = dataclass_field(default_factory=PlanRequest)
    create: CreateWorkspaceRequest | None = None


@dataclass(frozen=True, slots=True)
class PipelineEvent:
    stage: PipelineStage
    current: int | None = None
    total: int | None = None
    message: str | None = None
    source: object | None = None


@dataclass(frozen=True, slots=True)
class PipelineResult:
    workspace: WorkspaceRef
    compile: Any
    render_plan: RenderPlanResult
    render: Any
    compose: Any
    export: Any | None
    variant: str


@dataclass(frozen=True, slots=True)
class RenderExecutionOptions:
    """Explicit controls for destructive pipeline stage reruns and export tools."""

    force_stages: tuple[PipelineStage, ...] = ()
    restart_from: PipelineStage | None = None
    reset_stale_output: bool = False
    ffmpeg: Path | None = None
    ffprobe: Path | None = None


Metadata = Mapping[str, str]


WorkflowStage = Literal["convert", "compile", "render_plan", "render", "compose", "export"]
WorkflowAction = Literal["reuse", "execute", "replace", "blocked"]
WorkflowState = Literal["not_started", "partial", "complete", "stale", "blocked", "error"]
WorkflowInspectionState = WorkflowState | Literal["unknown"]
RebuildStage = Literal["convert", "compile", "render_plan", "render", "compose", "export"]
PauseTiming = Literal["fixed", "natural", "engaging"]


def _validate_pause_timing(value: str) -> None:
    if value not in {"fixed", "natural", "engaging"}:
        raise ValueError("pause_timing must be one of: fixed, natural, engaging")


@dataclass(frozen=True, slots=True)
class ProjectionOptions:
    pause_timing: PauseTiming = "fixed"

    def __post_init__(self) -> None:
        _validate_pause_timing(self.pause_timing)


@dataclass(frozen=True)
class PlanningOptions:
    planner_config: Any | None = None
    diagnostics: DiagnosticsMode = "errors"
    force: bool = False


@dataclass(frozen=True)
class VoiceRenderOptions:
    speech: str = "all"
    cache_dir: Path | None = None
    emphasis_policy: str = "reject"
    fail_fast: bool = False


@dataclass(frozen=True)
class CompositionOptions:
    pause_policy: Any | None = None
    output_policy: Any | None = None
    options: Any | None = None
    renderplan_root: Path | None = None
    voicerender_root: Path | None = None
    output_root: Path | None = None
    media_root: Path | None = None
    save_audiojobs: Path | None = None
    emphasis_policy: str = "segment_fallback"
    fail_fast: bool = False


@dataclass(frozen=True)
class ExportOptions:
    metadata: Mapping[str, str] | None = None
    cover: Path | None = None
    timeline: Path | None = None
    audiocompose_root: Path | None = None
    output_root: Path | None = None
    ffmpeg: Path | None = None
    ffprobe: Path | None = None


@dataclass(frozen=True, slots=True)
class SpeakRequest:
    text: str
    profile: Path | None = None
    role: str | None = None
    voice: str | None = None
    engine: str | None = None
    target: str | None = None
    language: str | None = None
    speaker: str | int | None = None
    engine_options: Mapping[str, Any] = dataclass_field(default_factory=dict)
    offline: bool = False
    refresh: bool = False

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("text must not be empty or whitespace-only")
        if self.profile is not None and any(
            value is not None for value in (self.engine, self.target, self.voice)
        ):
            raise ValueError(
                "profile is mutually exclusive with direct engine, target, and voice arguments"
            )
        if self.role is not None and self.profile is None:
            raise ValueError("--role requires --profile")
        object.__setattr__(self, "engine_options", MappingProxyType(dict(self.engine_options)))


@dataclass(frozen=True, slots=True)
class AudioBuffer:
    """Validated in-memory audio and WAV transport boundary."""

    audio: np.ndarray
    sample_rate: int

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        audio = np.asarray(self.audio)
        if audio.ndim not in {1, 2} or (audio.ndim == 2 and audio.shape[1] <= 0):
            raise ValueError(
                "audio must be a one- or two-dimensional array with a valid channel dimension"
            )
        object.__setattr__(self, "audio", audio)

    @property
    def channels(self) -> int:
        return 1 if self.audio.ndim == 1 else int(self.audio.shape[1])

    @property
    def frames(self) -> int:
        return int(self.audio.shape[0])

    @property
    def duration_seconds(self) -> float:
        return self.frames / self.sample_rate

    def __len__(self) -> int:
        return self.frames

    def to_wav_bytes(self) -> bytes:
        """Encode the current float audio as an in-memory PCM16 WAV."""
        audio = np.asarray(self.audio, dtype=np.float32)
        pcm = np.rint(np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
        output = BytesIO()
        with wave.open(output, "wb") as stream:
            stream.setnchannels(self.channels)
            stream.setsampwidth(2)
            stream.setframerate(self.sample_rate)
            stream.writeframes(pcm.tobytes())
        return output.getvalue()

    def write_wav(self, path: str | Path, *, replace: bool = False) -> Path:
        """Write this buffer as PCM16 WAV, refusing existing paths unless allowed."""
        output_path = Path(path).expanduser()
        if not replace and (output_path.exists() or output_path.is_symlink()):
            raise OutputCollisionError(f"output already exists: {output_path}")
        payload = self.to_wav_bytes()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with output_path.open("wb" if replace else "xb") as stream:
                stream.write(payload)
        except FileExistsError as error:
            if not replace:
                raise OutputCollisionError(f"output already exists: {output_path}") from error
            raise
        except Exception:
            if not replace:
                output_path.unlink(missing_ok=True)
            raise
        return output_path


@dataclass(frozen=True, slots=True)
class SpeakResult:
    """Semantic speech result independent of VoiceRender's renderer data types."""

    audio: AudioBuffer
    profile_id: str
    warnings: tuple[str, ...] = ()
    elapsed_seconds: float | None = None

    @property
    def sample_rate(self) -> int:
        """Compatibility shortcut for callers that only need basic playback metadata."""
        return self.audio.sample_rate

    @property
    def duration_seconds(self) -> float:
        return self.audio.duration_seconds


@dataclass(frozen=True, slots=True)
class EngineInfo:
    id: str
    version: str | None
    installed: bool
    runnable: bool
    features: tuple[str, ...]
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class TargetInfo:
    id: str
    languages: tuple[str, ...]
    status: str
    runtime_available: bool
    default_voice: str | None
    voice_count: int
    qualities: tuple[str, ...]
    voice_mode: str | None
    sample_rate: int | None
    features: tuple[str, ...]
    reference_only: bool


@dataclass(frozen=True, slots=True)
class VoiceInfo:
    id: str
    language: str | None
    locale: str | None
    language_label: str | None
    gender: str
    kind: str | None
    default: bool
    runtime_available: bool


@dataclass(frozen=True, slots=True)
class EngineOptionInfo:
    name: str
    value_type: str | None
    required: bool
    default: object
    choices: tuple[object, ...]
    minimum: int | float | None
    maximum: int | float | None
    description: str
    identity_affecting: bool
    runtime_only: bool


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    type: str
    message: str
    code: str | None = None
    stage: str | None = None
    package: str | None = None
    selector: str | None = None
    details: Mapping[str, object] = dataclass_field(default_factory=dict)
    recoverable: bool = False
    suggested_action: str | None = None


class CancellationToken:
    """Thread-safe cooperative cancellation signal for synchronous workflows."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise WorkflowCancelledError()


@dataclass(frozen=True, slots=True)
class SourceChapter:
    number: int
    title: str
    source_id: str | None = None
    parent_id: str | None = None
    level: int | None = None
    char_count: int | None = None


@dataclass(frozen=True, slots=True)
class SourceInspection:
    source: Path
    kind: Literal["document", "book"]
    metadata: Mapping[str, object] = dataclass_field(default_factory=dict)
    chapters: tuple[SourceChapter, ...] = ()


CapabilityState = Literal["ready", "unavailable", "unknown"]


@dataclass(frozen=True, slots=True)
class SpecialistCapability:
    package: str
    version: str | None
    state: CapabilityState
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowCapabilities:
    conversion: CapabilityState
    planning: CapabilityState
    synthesis: CapabilityState
    composition: CapabilityState
    export: CapabilityState


@dataclass(frozen=True, slots=True)
class ExportFormatCapability:
    name: str
    extension: str
    available: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SourceCapabilities:
    source: SourceInspection
    chapter_selection: bool
    persistent_workspace: bool
    default_output_format: str


@dataclass(frozen=True, slots=True)
class SynthesisCapabilities:
    state: CapabilityState
    engines: tuple[EngineInfo, ...]
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ReadioCapabilities:
    readio_version: str
    specialists: tuple[SpecialistCapability, ...]
    workflow: WorkflowCapabilities
    engines: tuple[EngineInfo, ...]
    export_formats: tuple[ExportFormatCapability, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenderRequest:
    source: Path
    profile: Path
    chapters: str = "all"
    language: str | None = None
    bundle: Path | None = None
    variant: str | None = None
    output: Path | None = None
    format: str | None = None
    bitrate: str | int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    rebuild_from: RebuildStage | None = None
    replace_output: bool = False
    planning: PlanningOptions = dataclass_field(default_factory=PlanningOptions)
    voice_render: VoiceRenderOptions = dataclass_field(default_factory=VoiceRenderOptions)
    projection: ProjectionOptions = dataclass_field(default_factory=ProjectionOptions)
    preparation: PreparationOptions = dataclass_field(default_factory=PreparationOptions)
    composition: CompositionOptions = dataclass_field(default_factory=CompositionOptions)
    export: ExportOptions = dataclass_field(default_factory=ExportOptions)


WorkflowReasonCode = Literal[
    "artifact_missing",
    "artifact_partial",
    "artifact_current",
    "artifact_stale",
    "explicit_rebuild",
    "inspection_deferred",
    "dependency_not_ready",
    "specialist_blocked",
    "specialist_error",
    "specialist_state_unknown",
    "replacement_not_authorized",
    "replacement_not_safe",
    "output_replacement_required",
]


@dataclass(frozen=True, slots=True)
class WorkflowBlocker:
    code: str
    message: str
    owner: str | None = None
    recoverable: bool = True
    suggested_action: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowArtifactRef:
    kind: str
    owner: str
    path: Path | None = None
    identity: str | None = None
    state: WorkflowInspectionState | None = None


@dataclass(frozen=True)
class WorkflowStep:
    stage: WorkflowStage
    package: str
    action: WorkflowAction
    selector: str | None
    reason: str
    force: bool = False
    state: WorkflowInspectionState = "unknown"
    details: Mapping[str, object] = dataclass_field(default_factory=dict)
    reason_code: WorkflowReasonCode = "specialist_state_unknown"
    inputs: tuple[WorkflowArtifactRef, ...] = ()
    outputs: tuple[WorkflowArtifactRef, ...] = ()
    blockers: tuple[WorkflowBlocker, ...] = ()


@dataclass(frozen=True)
class WorkflowPlan:
    source: Path
    workspace: Path | None
    workspace_mode: WorkspaceMode
    variant: str | None
    output: Path | None
    steps: tuple[WorkflowStep, ...]
    warnings: tuple[str, ...] = ()
    schema_version: int = 1

    @property
    def executable(self) -> bool:
        return not any(step.action == "blocked" for step in self.steps)


@dataclass(frozen=True, slots=True)
class StageExecutionRecord:
    stage: WorkflowStage
    package: str
    action: WorkflowAction
    selector: str | None
    state_before: WorkflowInspectionState
    state_after: WorkflowInspectionState | None
    elapsed_seconds: float
    reason_code: WorkflowReasonCode


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    source: Path
    workspace: Path | None
    workspace_mode: WorkspaceMode
    selected_chapters: str
    profile: Path
    variant: str
    output: Path
    output_format: str
    stages: tuple[StageExecutionRecord, ...]
    specialist_versions: Mapping[str, str | None]
    warnings: tuple[str, ...]
    elapsed_seconds: float


@dataclass(frozen=True)
class RenderResult:
    output: Path
    workspace: Path | None
    workspace_mode: WorkspaceMode
    variant: str
    executed_stages: tuple[str, ...]
    reused_stages: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    report: ExecutionReport | None = None


@dataclass(frozen=True)
class WorkflowEvent:
    stage: WorkflowStage
    kind: str
    package: str | None = None
    selector: str | None = None
    current: int | None = None
    total: int | None = None
    message: str | None = None
    elapsed_seconds: float | None = None
    details: Mapping[str, object] = dataclass_field(default_factory=dict)
