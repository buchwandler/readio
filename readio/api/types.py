"""Request and result types for native workspace orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any, Literal

from readio.workspace import CreateWorkspaceRequest, WorkspaceRef

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


Metadata = Mapping[str, str]
