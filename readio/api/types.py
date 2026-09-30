"""Stable request, result, and value types for the public Readio API."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Generic, Literal, TypeAlias, TypeVar, cast

from ..audio import AudioSink, RenderSummary
from ..config import LanguageSettings, ReaderSettings, ReadioConfig
from ..document import InputDocument as Document
from ..document import document_from_file, document_from_text
from ..engines.base import EngineCapabilities
from ..formats import AudioFormat
from ..jsonutil import JsonScalar, JsonValue, json_value
from ..plan import (
    CompositionOptions,
    InputRequest,
    MasteringProfile,
    OutputRequest,
    PlanDiagnostic,
    PlanRequest,
    SynthesisRequest,
)
from ..plan import ReadioPlanV2 as ResolvedPlan
from ..role_targets import VoiceTarget

AudiobookExportFormat = Literal["m4b"]
AUDIOBOOK_EXPORT_FORMAT: AudiobookExportFormat = "m4b"
SUPPORTED_AUDIOBOOK_FORMATS: tuple[AudiobookExportFormat, ...] = (AUDIOBOOK_EXPORT_FORMAT,)
SUPPORTED_AUDIOBOOK_EXPORT_FORMATS = SUPPORTED_AUDIOBOOK_FORMATS


@dataclass(frozen=True, slots=True)
class DiscoveryOptions:
    offline: bool = False
    refresh: bool = False
    preference: Literal["auto", "github", "huggingface", "upstream"] = "auto"


class _UnsetValue:
    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


Unset: TypeAlias = _UnsetValue
UNSET = _UnsetValue()


@dataclass(frozen=True, slots=True)
class LanguageProfilePatch:
    model: str | None | Unset = UNSET
    source: str | None | Unset = UNSET
    quality: str | None | Unset = UNSET
    voice: str | None | Unset = UNSET
    lexicons: tuple[str, ...] | None | Unset = UNSET
    g2p_fallback: str | None | Unset = UNSET
    lexicon_data_policy: str | None | Unset = UNSET
    allow_experimental: bool | Unset = UNSET


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
    format: AudioFormat = "wav"
    output: Path | None = None
    bitrate: str | None = None
    force: bool = False


@dataclass(frozen=True, slots=True)
class AudiobookExportOptions:
    format: AudiobookExportFormat = AUDIOBOOK_EXPORT_FORMAT
    output: Path | None = None
    title: str | None = None
    author: str | None = None
    cover: Path | None = None
    bitrate: str | None = None
    force: bool = False


def _freeze_setting_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_setting_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_setting_value(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class ProjectSynthesisSettings:
    """Sparse, durable synthesis preferences for one project."""

    language: str | None = None
    model: str | None = None
    model_source: str | None = None
    quality: str | None = None
    voice: str | None = None
    lexicons: tuple[str, ...] | None = None
    speaker: str | int | None = None
    clear_lexicons: bool | None = None
    auto_lexicons: bool | None = None
    spacy: str | None = None
    short_sentence: str | None = None
    g2p_fallback: str | None = None
    lexicon_data_policy: str | None = None
    language_detection: str | None = None
    detect_languages: tuple[str, ...] | None = None
    allow_experimental: bool | None = None
    speed: float | None = None
    voice_level: str | None = None
    pause_mode: str | None = None
    unit: str | None = None
    offline: bool | None = None
    engine: str | None = None
    engine_options: Mapping[str, JsonValue] | None = None
    voice_file: Path | None = None

    def __post_init__(self) -> None:
        for name in ("lexicons", "detect_languages"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, tuple(value))
        if self.engine_options is not None:
            object.__setattr__(self, "engine_options", _freeze_setting_value(self.engine_options))


@dataclass(frozen=True, slots=True)
class ProjectSettings:
    """Detached immutable desired pipeline settings for a project."""

    synthesis: ProjectSynthesisSettings | None = None
    composition: CompositionOptions | None = None
    export: ExportOptions | None = None
    audiobook_export: AudiobookExportOptions | None = None


@dataclass(frozen=True, slots=True)
class ProjectSettingsPatch:
    """Patch project settings, using None to clear and UNSET to leave unchanged."""

    synthesis: ProjectSynthesisSettings | None | Unset = UNSET
    composition: CompositionOptions | None | Unset = UNSET
    export: ExportOptions | None | Unset = UNSET
    audiobook_export: AudiobookExportOptions | None | Unset = UNSET


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
    loudness: LoudnessSummary | None = None

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
class AudiobookProjectDescription:
    """Persisted chapter scope for an existing audiobook project."""

    project: ProjectRef
    source: Path
    chapters: tuple[AudiobookProjectChapter, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(
            dict[str, JsonValue],
            json_value(
                {
                    "project": self.project.to_dict(),
                    "source": self.source,
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
    command: str | None = None

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
class SynthesisResolution:
    """Effective project synthesis settings resolved without rendering audio."""

    engine: str
    language: str
    voice: str | None
    model: str | None
    model_source: str | None
    quality: str | None
    speed: float
    unit: str
    pause_mode: str
    voice_level: str | None
    spacy: str | None = None
    short_sentence: str | None = None
    provider: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    lexicons: tuple[str, ...] | None = None
    g2p_fallback: str | None = None
    lexicon_data_policy: str | None = None
    language_detection: str | None = None
    detect_languages: tuple[str, ...] | None = None
    allow_experimental: bool = False

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class LoudnessSummary:
    profile: MasteringProfile
    integrated_lufs_before: float | None
    integrated_lufs_after: float | None
    sample_peak_dbfs_before: float | None
    sample_peak_dbfs_after: float | None
    true_peak_dbtp_before: float | None
    true_peak_dbtp_after: float | None
    target_lufs: float | None
    true_peak_ceiling_dbtp: float | None
    requested_gain_db: float
    applied_gain_db: float
    target_reached: bool
    peak_policy: Literal["reduce_gain", "error"]
    warning: str | None = None
    analysis_seconds: float = 0.0
    gain_seconds: float = 0.0
    post_gain_metrics_seconds: float = 0.0

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> LoudnessSummary:
        def optional_float(name: str) -> float | None:
            raw = value.get(name)
            return None if raw is None else float(cast(float | int, raw))

        return cls(
            profile=cast(MasteringProfile, value.get("profile", "spoken-word")),
            integrated_lufs_before=optional_float("integrated_lufs_before"),
            integrated_lufs_after=optional_float("integrated_lufs_after"),
            sample_peak_dbfs_before=optional_float("sample_peak_dbfs_before"),
            sample_peak_dbfs_after=optional_float("sample_peak_dbfs_after"),
            true_peak_dbtp_before=optional_float("true_peak_dbtp_before"),
            true_peak_dbtp_after=optional_float("true_peak_dbtp_after"),
            target_lufs=optional_float("target_lufs"),
            true_peak_ceiling_dbtp=optional_float("true_peak_ceiling_dbtp"),
            requested_gain_db=float(value.get("requested_gain_db", 0.0)),
            applied_gain_db=float(value.get("applied_gain_db", 0.0)),
            target_reached=bool(value.get("target_reached", False)),
            peak_policy=cast(
                Literal["reduce_gain", "error"], value.get("peak_policy", "reduce_gain")
            ),
            warning=cast(str | None, value.get("warning")),
            analysis_seconds=float(value.get("analysis_seconds", 0.0)),
            gain_seconds=float(value.get("gain_seconds", 0.0)),
            post_gain_metrics_seconds=float(value.get("post_gain_metrics_seconds", 0.0)),
        )

    @classmethod
    def from_loudness_result(cls, profile: MasteringProfile, value: Any) -> LoudnessSummary:
        before = value.before
        after = value.after
        return cls.from_mapping(
            {
                "profile": profile,
                "integrated_lufs_before": before.integrated_lufs,
                "integrated_lufs_after": after.integrated_lufs,
                "sample_peak_dbfs_before": before.sample_peak_dbfs,
                "sample_peak_dbfs_after": after.sample_peak_dbfs,
                "true_peak_dbtp_before": before.true_peak_dbtp,
                "true_peak_dbtp_after": after.true_peak_dbtp,
                "target_lufs": value.target_lufs,
                "true_peak_ceiling_dbtp": value.true_peak_ceiling_dbtp,
                "requested_gain_db": value.requested_gain_db,
                "applied_gain_db": value.applied_gain_db,
                "target_reached": value.target_reached,
                "peak_policy": value.peak_policy,
                "warning": value.warning,
                "analysis_seconds": value.analysis_seconds,
                "gain_seconds": value.gain_seconds,
                "post_gain_metrics_seconds": value.post_gain_metrics_seconds,
            }
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class ProjectCompositionResult:
    project: ProjectRef
    composition_id: str
    frames: int
    items: int
    master_path: Path | None = None
    loudness: LoudnessSummary | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class ProjectExportResult:
    project: ProjectRef
    output_path: Path
    format: str
    output_sha256: str
    export_id: str

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json_value(self))


@dataclass(frozen=True, slots=True)
class AudiobookExportResult:
    project: ProjectRef
    output_path: Path
    format: AudiobookExportFormat
    output_sha256: str
    export_id: str
    chapter_count: int

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
    loudness: LoudnessSummary | None = None

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
    role: str
    target: VoiceTarget

    @property
    def provider(self) -> str | None:
        return self.target.provider

    @property
    def engine(self) -> str:
        return self.target.engine

    @property
    def voice(self) -> str:
        return self.target.voice

    @property
    def target_id(self) -> str | None:
        return self.target.target_id

    @property
    def selector(self) -> str | None:
        return self.target.selector

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(
            dict[str, JsonValue],
            json_value({"role": self.role, **self.target.to_dict(), "provider": self.provider}),
        )


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
    project_target: VoiceTarget | None = None
    config_target: VoiceTarget | None = None
    effective_target: VoiceTarget | None = None

    @property
    def scope_count(self) -> int:
        return len(self.locations)

    @property
    def document_binding(self) -> str | None:
        values = set(self.document_bindings.values())
        return next(iter(values)) if len(values) == 1 else None

    @property
    def effective_engine(self) -> str | None:
        return self.effective_target.engine if self.effective_target else None

    @property
    def effective_provider(self) -> str | None:
        return self.effective_target.provider if self.effective_target else None

    def to_dict(self) -> dict[str, JsonValue]:
        result = cast(dict[str, JsonValue], json_value(self))
        for name in ("project_target", "config_target", "effective_target"):
            target = getattr(self, name)
            if target is not None:
                result[name] = cast(
                    JsonValue,
                    json_value({**target.to_dict(), "provider": target.provider}),
                )
        return result


@dataclass(frozen=True, slots=True)
class ProjectRoleInspection:
    provider: str | None
    roles: tuple[ProjectRole, ...]

    @property
    def unresolved(self) -> tuple[str, ...]:
        return tuple(role.role for role in self.roles if role.effective_voice is None)

    def to_dict(self) -> dict[str, JsonValue]:
        return cast(
            dict[str, JsonValue],
            json_value(
                {
                    "provider": self.provider,
                    "roles": [role.to_dict() for role in self.roles],
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class ProjectRoleMutationResult:
    project: ProjectRef
    role: str
    previous_project_binding: str | None
    project_binding: str | None
    effective_voice: str | None
    origin: str | None
    status: str
    previous_project_target: VoiceTarget | None = None
    project_target: VoiceTarget | None = None
    effective_target: VoiceTarget | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        result = cast(dict[str, JsonValue], json_value(self))
        for name in ("previous_project_target", "project_target", "effective_target"):
            target = getattr(self, name)
            if target is not None:
                result[name] = cast(
                    JsonValue,
                    json_value({**target.to_dict(), "provider": target.provider}),
                )
        return result


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
    "AUDIOBOOK_EXPORT_FORMAT",
    "SUPPORTED_AUDIOBOOK_EXPORT_FORMATS",
    "SUPPORTED_AUDIOBOOK_FORMATS",
    "UNSET",
    "AudioFormat",
    "AudioFormatDiagnostic",
    "AudioFormatInfo",
    "AudioSink",
    "AudiobookChapter",
    "AudiobookExportFormat",
    "AudiobookExportOptions",
    "AudiobookExportResult",
    "AudiobookInspection",
    "AudiobookProjectChapter",
    "AudiobookProjectDescription",
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
    "LanguageProfilePatch",
    "LanguageProfileResolution",
    "LanguageSettings",
    "LexiconInfo",
    "LexiconQuery",
    "LoudnessSummary",
    "MasteringProfile",
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
    "ProjectRoleMutationResult",
    "ProjectSettings",
    "ProjectSettingsPatch",
    "ProjectStatus",
    "ProjectSynthesisResult",
    "ProjectSynthesisSettings",
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
    "SynthesisResolution",
    "SynthesisTargetInfo",
    "TargetQuery",
    "TemplateInfo",
    "TemplateValidationResult",
    "Unset",
    "VoiceInfo",
    "VoiceQuery",
    "VoiceResolution",
    "document_from_file",
    "document_from_text",
]
