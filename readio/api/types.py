"""Stable request, result, and value types for the public Readio API."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Generic, Literal, TypeVar, cast

from ..audio import AudioSink, RenderSummary
from ..config import LanguageSettings, ReaderSettings, ReadioConfig
from ..document import InputDocument as Document
from ..document import document_from_file, document_from_text
from ..engines.base import EngineCapabilities
from ..jsonutil import JsonScalar, JsonValue, json_value
from ..plan import (
    CompositionOptions,
    InputRequest,
    OutputRequest,
    PlanDiagnostic,
    PlanRequest,
    SynthesisRequest,
)
from ..plan import ReadioPlanV2 as ResolvedPlan


@dataclass(frozen=True, slots=True)
class DiscoveryOptions:
    offline: bool = False
    refresh: bool = False
    preference: Literal["auto", "github", "huggingface", "upstream"] = "auto"


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CatalogDiscovery:
    registry_source: str
    cache_fallback: bool = False
    offline: bool = False
    refreshed: bool = False

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(
            dict[str, JsonValue],
            json_value(
                {
                    "source": self.registry_source,
                    "registry_source": self.registry_source,
                    "cache_fallback": self.cache_fallback,
                    "offline": self.offline,
                    "refreshed": self.refreshed,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class CatalogListing(Generic[T]):
    items: tuple[T, ...]
    discovery: CatalogDiscovery


@dataclass(frozen=True, slots=True)
class ExportOptions:
    format: str = "wav"
    output: Path | None = None
    bitrate: str | None = None
    force: bool = False


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    field: str | None = None
    source_path: Path | None = None
    line: int | None = None
    details: Mapping[str, JsonValue] = dataclass_field(default_factory=dict)

    @classmethod
    def from_plan(cls, diagnostic: PlanDiagnostic) -> Diagnostic:
        return cls(
            code=diagnostic.code,
            severity=diagnostic.severity,
            message=diagnostic.message,
            field=diagnostic.field,
            source_path=diagnostic.source_path,
            line=diagnostic.line,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class RenderResult:
    plan: ResolvedPlan | None
    summary: RenderSummary
    output_path: Path | None = None
    manifest_path: Path | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    audio_format: str | None = None
    manifest_schema: str | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class ConfigurationInitResult:
    path: Path
    created_directories: tuple[Path, ...]
    seeded_templates: tuple[Path, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class LanguageProfileResolution:
    requested: str
    normalized: str
    matched_key: str | None
    match: Literal["exact", "base"] | None
    settings: LanguageSettings | None

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class ProjectRef:
    root: Path
    project_id: str
    name: str
    kind: str
    source_format: str

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class AudiobookProjectChapter:
    number: int
    scope_id: str
    title: str
    level: int

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class AudiobookProjectResult:
    project: ProjectRef
    source: Path
    chapters: tuple[AudiobookProjectChapter, ...]

    @property
    def selected_chapters(self) -> int:
        return len(self.chapters)

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(
            dict[str, JsonValue],
            json_value(
                {
                    "project": self.project.to_dict(),
                    "source": self.source,
                    "selected_chapters": self.selected_chapters,
                    "chapters": self.chapters,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class AudiobookChapter:
    number: int
    source_id: str
    title: str
    href: str | None
    parent_id: str | None
    level: int
    char_count: int
    markdown: str
    diagnostics: tuple[Diagnostic, ...] = ()

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class AudiobookInspection:
    source: Path
    metadata: Mapping[str, JsonValue]
    chapters: tuple[AudiobookChapter, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


ProjectLike = ProjectRef | Path | str

StageName = Literal[
    "source",
    "document",
    "plan",
    "synthesis",
    "composition",
    "output",
]


@dataclass(frozen=True, slots=True)
class StageStatus:
    stage: StageName
    state: Literal["current", "stale", "missing", "invalid", "blocked"]
    reason: str
    blocked_by: StageName | None = None
    details: Mapping[str, JsonValue] = dataclass_field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class NextAction:
    stage: StageName
    reason: str

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class ProjectStatus:
    project: ProjectRef
    stages: tuple[StageStatus, ...]
    issues: tuple[Diagnostic, ...]
    next_actions: tuple[NextAction, ...]

    def stage(self, name: StageName) -> StageStatus:
        for status in self.stages:
            if status.stage == name:
                return status
        raise KeyError(name)

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class StageOperation:
    stage: str
    action: Literal["skipped", "rebuilt", "reused", "created"]
    details: Mapping[str, JsonValue] = dataclass_field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class ProjectPlanScope:
    scope_id: str
    plan_id: str
    sha256: str | None = None
    units: int | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class ProjectPlanResult:
    project: ProjectRef
    scopes: tuple[ProjectPlanScope, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class ProjectSynthesisResult:
    project: ProjectRef
    profile_id: str
    plan_ids: tuple[ProjectPlanScope, ...]
    reused: int
    rendered: int
    activated: bool
    selected_units: int = 0

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class ProjectCompositionResult:
    project: ProjectRef
    composition_id: str
    frames: int
    items: int
    master_path: Path | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class ProjectExportResult:
    project: ProjectRef
    output_path: Path
    format: str
    output_sha256: str

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class ProjectBuildResult:
    project: ProjectRef
    operations: tuple[StageOperation, ...]
    output_path: Path | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


ProjectTarget = Literal["plan", "synthesis", "composition", "export"]


@dataclass(frozen=True, slots=True)
class ProjectBuildRequest:
    target: ProjectTarget = "export"
    selection: str = "all"
    voice_bindings: Mapping[str, str] = dataclass_field(default_factory=dict)
    synthesis: SynthesisRequest = dataclass_field(default_factory=SynthesisRequest)
    composition: CompositionOptions = dataclass_field(default_factory=CompositionOptions)
    export: ExportOptions = dataclass_field(default_factory=ExportOptions)


@dataclass(frozen=True, slots=True)
class PreviewRequest:
    selection: str = "first:3"
    synthesis: SynthesisRequest = dataclass_field(default_factory=SynthesisRequest)
    voice_bindings: Mapping[str, str] = dataclass_field(default_factory=dict)
    composition: CompositionOptions = dataclass_field(default_factory=CompositionOptions)
    output: Path | None = None
    activate: bool = False


@dataclass(frozen=True, slots=True)
class PreviewResult:
    project: ProjectRef
    profile_id: str
    plan_ids: tuple[ProjectPlanScope, ...]
    reused: int
    rendered: int
    activated: bool
    sample_rate: int
    frames: int
    items: int
    output_path: Path | None = None
    composition_id: str | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class EngineInfo:
    id: str
    version: str | None
    registered: bool
    installed: bool
    runnable: bool
    capabilities: EngineCapabilities | None = None
    missing_dependency: str | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class SynthesisTargetInfo:
    engine: str
    id: str
    display_name: str
    languages: tuple[str, ...] = ()
    status: str = "ready"
    runtime_available: bool = True
    sample_rate: int | None = None
    voices: tuple[str, ...] = ()
    speakers: tuple[str, ...] = ()
    qualities: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    capabilities: frozenset[str] = frozenset()
    metadata: Mapping[str, JsonValue] = dataclass_field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class TargetQuery:
    engine: str | None = None
    language: str | None = None
    status: str | None = None
    runnable_only: bool = False


@dataclass(frozen=True, slots=True)
class ModelQuery:
    language: str | None = None
    status: str | None = None
    engine: str | None = None


@dataclass(frozen=True, slots=True)
class ModelVoiceInfo:
    id: str
    gender: str
    language: str
    locale: str
    language_label: str

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class ModelInfo:
    id: str
    source: str
    languages: tuple[str, ...]
    voices: tuple[str, ...]
    default_voice: str
    qualities: tuple[str, ...]
    g2p_backend: str | None
    lexicons: tuple[str, ...] | None
    frontend: str
    status: str
    experimental: bool
    runtime_available: bool
    redistribution_allowed: bool
    distribution_id: str | None = None
    provider: str | None = None
    distribution_provider: str | None = None
    backend: str = "pykokoro"
    sample_rate: int | None = None
    max_tokens: int | None = None
    voice_details: tuple[ModelVoiceInfo, ...] = ()

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class VoiceQuery:
    language: str | None = None
    gender: str | None = None
    model: str | None = None
    engine: str | None = None


@dataclass(frozen=True, slots=True)
class VoiceInfo:
    selector: str | None
    id: str
    gender: str
    language: str
    locale: str
    language_label: str
    model: str
    source: str
    default: bool
    status: str
    experimental: bool
    runtime_available: bool
    distribution_id: str | None = None
    provider: str | None = None
    engine: str = "pykokoro"
    slot: int | None = None
    selector_language: str | None = None
    selector_engine_code: str | None = None

    @property
    def qualified_id(self) -> str:
        return f"{self.engine}:{self.model}:{self.id}"

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class VoiceResolution:
    requested: str
    selector: str | None
    language: str | None
    model: str | None
    source: str | None
    voice: str
    engine: str | None = None
    catalog_entry: VoiceInfo | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class LexiconQuery:
    language: str | None = None
    model: str | None = None
    engine: str | None = None


@dataclass(frozen=True, slots=True)
class LexiconInfo:
    selector: str
    engine: str
    language: str
    locale: str
    asset_id: str | None
    data_backend: str | None
    default: bool
    installed: bool | None
    models: tuple[str, ...] = ()
    model_support: str = "unknown"
    display_name: str | None = None
    phoneme_encoding: str | None = None
    data_version: str | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class AudioFormatInfo:
    id: str
    suffix: str
    available: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class RoleBinding:
    provider: str
    role: str
    voice: str

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class RoleLocation:
    scope_id: str
    lines: tuple[int, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class ProjectRole:
    role: str
    uses: int
    locations: tuple[RoleLocation, ...]
    document_bindings: Mapping[str, str]
    project_binding: str | None
    config_binding: str | None
    effective_voice: str | None
    origin: str
    status: str
    effective_by_scope: Mapping[str, Mapping[str, JsonValue]]

    @property
    def scope_count(self) -> int:
        return len(self.locations)

    @property
    def document_binding(self) -> str | None:
        values = set(self.document_bindings.values())
        return next(iter(values)) if len(values) == 1 else None

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class ProjectRoleInspection:
    provider: str
    roles: tuple[ProjectRole, ...]

    @property
    def unresolved(self) -> tuple[str, ...]:
        return tuple(role.role for role in self.roles if role.effective_voice is None)

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class SSMDVoiceReference:
    reference: str
    count: int
    lines: tuple[int, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class SSMDAnalysis:
    provider: str
    source_path: Path | None
    document_bindings: Mapping[str, str]
    default_bindings: Mapping[str, str]
    runtime_bindings: Mapping[str, str]
    voice_references: tuple[SSMDVoiceReference, ...]
    unresolved_references: tuple[str, ...]
    diagnostics: tuple[Diagnostic, ...]

    @property
    def ok(self) -> bool:
        return not self.unresolved_references and not any(
            item.severity == "error" for item in self.diagnostics
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class SSMDCheckResult:
    source_path: Path | None
    analysis: SSMDAnalysis
    roundtrip: Mapping[str, JsonValue] | None = None

    @property
    def ok(self) -> bool:
        return self.analysis.ok and (
            self.roundtrip is None or self.roundtrip.get("ok") is not False
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class SSMDMaterializeResult:
    source_path: Path
    output_path: Path
    provider: str
    binding_count: int
    in_place: bool

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class TemplateInfo:
    name: str
    path: Path

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class TemplateValidationResult:
    name: str
    source_path: Path
    ok: bool
    analysis: SSMDAnalysis | None = None
    roundtrip: Mapping[str, JsonValue] | None = None
    consumer: SSMDAnalysis | None = None
    error: Diagnostic | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class EngineDiagnostic:
    id: str
    adapter_available: bool
    package_available: bool
    version: str | None
    status: Literal["ready", "missing_dependency", "unavailable"]
    missing_dependency: str | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class AudioFormatDiagnostic:
    id: str
    suffix: str
    available: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class DependencyDiagnostic:
    id: str
    available: bool
    version: str | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class PathDiagnostic:
    name: str
    path: Path
    exists: bool

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class DoctorReport:
    readio_version: str
    python_version: str
    platform: str
    config_path: Path
    config_exists: bool
    engines: tuple[EngineDiagnostic, ...]
    dependencies: tuple[DependencyDiagnostic, ...]
    audio_formats: tuple[AudioFormatDiagnostic, ...]
    paths: tuple[PathDiagnostic, ...]
    voice_provider: str

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


__all__ = [
    "AudioFormatDiagnostic",
    "AudioFormatInfo",
    "AudioSink",
    "AudiobookChapter",
    "AudiobookInspection",
    "AudiobookProjectChapter",
    "AudiobookProjectResult",
    "CompositionOptions",
    "ConfigurationInitResult",
    "DependencyDiagnostic",
    "Diagnostic",
    "DiscoveryOptions",
    "DoctorReport",
    "Document",
    "EngineDiagnostic",
    "EngineInfo",
    "ExportOptions",
    "InputRequest",
    "JsonScalar",
    "JsonValue",
    "LanguageProfileResolution",
    "LanguageSettings",
    "LexiconInfo",
    "LexiconQuery",
    "ModelInfo",
    "ModelQuery",
    "ModelVoiceInfo",
    "NextAction",
    "OutputRequest",
    "PathDiagnostic",
    "PlanDiagnostic",
    "PlanRequest",
    "PreviewRequest",
    "PreviewResult",
    "ProjectBuildRequest",
    "ProjectBuildResult",
    "ProjectCompositionResult",
    "ProjectExportResult",
    "ProjectLike",
    "ProjectPlanResult",
    "ProjectPlanScope",
    "ProjectRef",
    "ProjectRole",
    "ProjectRoleInspection",
    "ProjectStatus",
    "ProjectSynthesisResult",
    "ProjectTarget",
    "ReaderSettings",
    "ReadioConfig",
    "RenderResult",
    "RenderSummary",
    "ResolvedPlan",
    "RoleBinding",
    "RoleLocation",
    "SSMDAnalysis",
    "SSMDCheckResult",
    "SSMDMaterializeResult",
    "SSMDVoiceReference",
    "StageName",
    "StageOperation",
    "StageStatus",
    "SynthesisRequest",
    "SynthesisTargetInfo",
    "TargetQuery",
    "TemplateInfo",
    "TemplateValidationResult",
    "VoiceInfo",
    "VoiceQuery",
    "VoiceResolution",
    "document_from_file",
    "document_from_text",
]
