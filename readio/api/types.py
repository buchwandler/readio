"""Request and result types for native workspace orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any, Literal

import numpy as np

from readio.workspace import (
    CreateWorkspaceRequest,
    WorkspaceKind,
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


@dataclass(frozen=True, slots=True)
class RenderIntentPlan:
    """Read-only status and execution outline for a resolved intent."""

    resolved: Any
    status: BundleStatus | None
    stages: tuple[str, ...]
    convert_required: bool
    output_exists: bool


@dataclass(frozen=True, slots=True)
class RenderIntentResult:
    """Final output and specialist stage results for an intent-driven render."""

    workspace: WorkspaceRef
    kind: WorkspaceKind
    source: Path
    profile: Path
    variant: str
    output: Path
    pipeline: PipelineResult
    converted: bool
    elapsed_seconds: float


Metadata = Mapping[str, str]


WorkflowStage = Literal["convert", "compile", "render_plan", "render", "compose", "export"]
WorkflowAction = Literal["reuse", "execute", "replace", "blocked"]
WorkflowState = Literal["not_started", "partial", "complete", "stale", "blocked", "error"]
WorkflowInspectionState = WorkflowState | Literal["unknown"]
RebuildStage = Literal["convert", "compile", "render_plan", "render", "compose", "export"]


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


@dataclass(frozen=True)
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


@dataclass(frozen=True, slots=True)
class AudioBuffer:
    """Stable audio payload returned by semantic in-memory speech."""

    audio: np.ndarray
    sample_rate: int

    @property
    def channels(self) -> int:
        if self.audio.ndim == 1:
            return 1
        if self.audio.ndim == 2 and self.audio.shape[1] > 0:
            return int(self.audio.shape[1])
        raise ValueError("audio must be a non-empty one- or two-dimensional array")

    def __len__(self) -> int:
        return int(self.audio.shape[0]) if self.audio.ndim in {1, 2} else 0


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
    composition: CompositionOptions = dataclass_field(default_factory=CompositionOptions)
    export: ExportOptions = dataclass_field(default_factory=ExportOptions)


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


@dataclass(frozen=True)
class WorkflowPlan:
    source: Path
    workspace: Path | None
    workspace_mode: WorkspaceMode
    variant: str | None
    output: Path | None
    steps: tuple[WorkflowStep, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenderResult:
    output: Path
    workspace: Path | None
    workspace_mode: WorkspaceMode
    variant: str
    executed_stages: tuple[str, ...]
    reused_stages: tuple[str, ...]
    warnings: tuple[str, ...] = ()


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
