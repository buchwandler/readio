"""Serializable models for persistent Readio projects.

The project format deliberately keeps semantic, acoustic, composition, and
encoding identities separate.  Loaders are strict about the format and schema
markers so corrupted or unrelated directories fail early.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


class ProjectFormatError(ValueError):
    """Raised when a persisted project artifact is malformed."""


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectFormatError(f"{name} must be an object")
    return value


def _validate_project_settings(value: Any) -> None:
    settings = _require_mapping(value, "project.settings")
    if "ssmd" not in settings:
        return
    ssmd = _require_mapping(settings["ssmd"], "project.settings.ssmd")
    if "voice_bindings" not in ssmd:
        return
    bindings = _require_mapping(
        ssmd["voice_bindings"], "project.settings.ssmd.voice_bindings"
    )
    for provider, raw_roles in bindings.items():
        _require_string(provider, "project.settings.ssmd.voice_bindings provider")
        roles = _require_mapping(
            raw_roles, f"project.settings.ssmd.voice_bindings.{provider}"
        )
        for role, voice in roles.items():
            _require_string(
                role, f"project.settings.ssmd.voice_bindings.{provider} role"
            )
            _require_string(
                voice, f"project.settings.ssmd.voice_bindings.{provider}.{role}"
            )


def _require_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProjectFormatError(f"{name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class PlanScope:
    id: str
    kind: str
    path: str
    title: str | None = None
    plan_id: str | None = None
    sha256: str | None = None
    document_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "kind": self.kind, "path": self.path}
        for key, value in (
            ("title", self.title),
            ("plan_id", self.plan_id),
            ("sha256", self.sha256),
            ("document_sha256", self.document_sha256),
        ):
            if value is not None:
                result[key] = value
        return result

    @classmethod
    def from_dict(cls, value: Any) -> PlanScope:
        data = _require_mapping(value, "plan scope")
        return cls(
            id=_require_string(data.get("id"), "scope.id"),
            kind=_require_string(data.get("kind"), "scope.kind"),
            path=_require_string(data.get("path"), "scope.path"),
            title=data.get("title"),
            plan_id=data.get("plan_id"),
            sha256=data.get("sha256"),
            document_sha256=data.get("document_sha256"),
        )


@dataclass(frozen=True, slots=True)
class DocumentScope:
    id: str
    kind: str
    path: str
    input_format: str
    title: str | None = None
    source_number: int | None = None
    source_id: str | None = None
    href: str | None = None
    parent_id: str | None = None
    level: int | None = None
    char_count: int | None = None
    extracted_sha256: str | None = None
    diagnostics: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "path": self.path,
            "input_format": self.input_format,
        }
        for key, value in (
            ("title", self.title),
            ("source_number", self.source_number),
            ("source_id", self.source_id),
            ("href", self.href),
            ("parent_id", self.parent_id),
            ("level", self.level),
            ("char_count", self.char_count),
            ("extracted_sha256", self.extracted_sha256),
        ):
            if value is not None:
                result[key] = value
        if self.diagnostics:
            result["diagnostics"] = [dict(item) for item in self.diagnostics]
        return result

    @classmethod
    def from_dict(cls, value: Any) -> DocumentScope:
        data = _require_mapping(value, "document scope")
        diagnostics = data.get("diagnostics", [])
        if not isinstance(diagnostics, list) or any(
            not isinstance(item, Mapping) for item in diagnostics
        ):
            raise ProjectFormatError("document scope diagnostics must be a list of objects")
        optional_ints = {}
        for key in ("source_number", "level", "char_count"):
            item = data.get(key)
            if item is not None and (not isinstance(item, int) or isinstance(item, bool)):
                raise ProjectFormatError(f"document scope {key} must be an integer")
            optional_ints[key] = item
        optional_strings = {}
        for key in ("title", "source_id", "href", "parent_id", "extracted_sha256"):
            item = data.get(key)
            if item is not None and not isinstance(item, str):
                raise ProjectFormatError(f"document scope {key} must be a string")
            optional_strings[key] = item
        return cls(
            id=_require_string(data.get("id"), "document scope.id"),
            kind=_require_string(data.get("kind"), "document scope.kind"),
            path=_require_string(data.get("path"), "document scope.path"),
            input_format=_require_string(data.get("input_format"), "document scope.input_format"),
            **optional_strings,
            **optional_ints,
            diagnostics=tuple(dict(item) for item in diagnostics),
        )


@dataclass(frozen=True, slots=True)
class DocumentIndex:
    scopes: tuple[DocumentScope, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    selection: tuple[int, ...] = ()
    schema_version: int = 1
    format: str = "readio.document-index"

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "schema_version": self.schema_version,
            "scopes": [scope.to_dict() for scope in self.scopes],
            "metadata": dict(self.metadata),
            "selection": list(self.selection),
        }

    @classmethod
    def from_dict(cls, value: Any) -> DocumentIndex:
        data = _require_mapping(value, "document index")
        if data.get("format") != "readio.document-index":
            raise ProjectFormatError("document index has an unexpected format")
        if data.get("schema_version") != 1:
            raise ProjectFormatError("unsupported document index schema_version")
        raw_scopes = data.get("scopes")
        if not isinstance(raw_scopes, list) or not raw_scopes:
            raise ProjectFormatError("document index scopes must be a non-empty list")
        scopes = tuple(DocumentScope.from_dict(item) for item in raw_scopes)
        if len({scope.id for scope in scopes}) != len(scopes):
            raise ProjectFormatError("document index contains duplicate scope IDs")
        metadata = data.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ProjectFormatError("document index metadata must be an object")
        selection = data.get("selection", [])
        if not isinstance(selection, list) or any(
            not isinstance(item, int) or isinstance(item, bool) for item in selection
        ):
            raise ProjectFormatError("document index selection must be a list of integers")
        return cls(
            scopes=scopes,
            metadata=dict(metadata),
            selection=tuple(selection),
        )


@dataclass(frozen=True, slots=True)
class PlanIndex:
    scopes: tuple[PlanScope, ...]
    schema_version: int = 1
    format: str = "readio.plan-index"

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "schema_version": self.schema_version,
            "scopes": [scope.to_dict() for scope in self.scopes],
        }

    @classmethod
    def from_dict(cls, value: Any) -> PlanIndex:
        data = _require_mapping(value, "plan index")
        if data.get("format") != "readio.plan-index":
            raise ProjectFormatError("plan index has an unexpected format")
        if data.get("schema_version") != 1:
            raise ProjectFormatError("unsupported plan index schema_version")
        scopes = data.get("scopes")
        if not isinstance(scopes, list) or not scopes:
            raise ProjectFormatError("plan index scopes must be a non-empty list")
        return cls(tuple(PlanScope.from_dict(item) for item in scopes))


@dataclass(frozen=True, slots=True)
class ProjectManifest:
    project_id: str
    name: str
    source_path: str
    source_format: str
    source_sha256: str
    document_metadata_path: str = "document/metadata.json"
    document_text_path: str = "document/document.txt"
    document_index_path: str = "document/index.json"
    plan_index_path: str = "plan/index.json"
    synthesis_profile_path: str = "synthesis/profile.json"
    synthesis_trace_path: str = "synthesis/trace.json"
    composition_audiojob_path: str = "composition/audiojob.json"
    composition_state_path: str = "composition/state.json"
    composition_master_path: str = "composition/master.wav"
    composition_timeline_path: str = "composition/timeline.json"
    outputs: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    settings: Mapping[str, Any] = field(default_factory=dict)
    kind: str = "document"
    schema_version: int = 2
    format: str = "readio.project"

    def __post_init__(self) -> None:
        _validate_project_settings(self.settings)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "format": self.format,
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "name": self.name,
            "source": {
                "path": self.source_path,
                "format": self.source_format,
                "sha256": self.source_sha256,
            },
            "plan": {"index_path": self.plan_index_path},
            "active_synthesis": {
                "profile_path": self.synthesis_profile_path,
                "trace_path": self.synthesis_trace_path,
            },
            "composition": {
                "audiojob_path": self.composition_audiojob_path,
                "state_path": self.composition_state_path,
                "master_path": self.composition_master_path,
                "timeline_path": self.composition_timeline_path,
            },
            "outputs": {key: dict(value) for key, value in self.outputs.items()},
            "settings": dict(self.settings),
        }
        if self.schema_version == 1:
            result["document"] = {
                "metadata_path": self.document_metadata_path,
                "text_path": self.document_text_path,
            }
        else:
            result["kind"] = self.kind
            result["document"] = {"index_path": self.document_index_path}
        return result

    @classmethod
    def from_dict(cls, value: Any) -> ProjectManifest:
        data = _require_mapping(value, "project manifest")
        if data.get("format") != "readio.project":
            raise ProjectFormatError("project.json has an unexpected format")
        schema_version = data.get("schema_version")
        if schema_version not in {1, 2}:
            raise ProjectFormatError("unsupported project schema_version")
        source = _require_mapping(data.get("source"), "project.source")
        document = _require_mapping(data.get("document"), "project.document")
        plan = _require_mapping(data.get("plan"), "project.plan")
        synthesis = _require_mapping(data.get("active_synthesis"), "project.active_synthesis")
        composition = _require_mapping(data.get("composition"), "project.composition")
        outputs = data.get("outputs", {})
        if not isinstance(outputs, Mapping):
            raise ProjectFormatError("project.outputs must be an object")
        settings = data.get("settings", {})
        if not isinstance(settings, Mapping):
            raise ProjectFormatError("project.settings must be an object")
        if schema_version == 1:
            document_metadata_path = _require_string(
                document.get("metadata_path"), "document.metadata_path"
            )
            document_text_path = _require_string(document.get("text_path"), "document.text_path")
            document_index_path = "document/index.json"
            kind = "document"
        else:
            document_index_path = _require_string(document.get("index_path"), "document.index_path")
            document_metadata_path = "document/metadata.json"
            document_text_path = "document/document.txt"
            kind = _require_string(data.get("kind"), "kind")
        return cls(
            project_id=_require_string(data.get("project_id"), "project_id"),
            name=_require_string(data.get("name"), "name"),
            source_path=_require_string(source.get("path"), "source.path"),
            source_format=_require_string(source.get("format"), "source.format"),
            source_sha256=_require_string(source.get("sha256"), "source.sha256"),
            document_metadata_path=document_metadata_path,
            document_text_path=document_text_path,
            document_index_path=document_index_path,
            plan_index_path=_require_string(plan.get("index_path"), "plan.index_path"),
            synthesis_profile_path=_require_string(
                synthesis.get("profile_path"), "active_synthesis.profile_path"
            ),
            synthesis_trace_path=_require_string(
                synthesis.get("trace_path"), "active_synthesis.trace_path"
            ),
            composition_audiojob_path=_require_string(
                composition.get("audiojob_path"), "composition.audiojob_path"
            ),
            composition_state_path=_require_string(
                composition.get("state_path"), "composition.state_path"
            ),
            composition_master_path=_require_string(
                composition.get("master_path"), "composition.master_path"
            ),
            composition_timeline_path=_require_string(
                composition.get("timeline_path"), "composition.timeline_path"
            ),
            outputs=outputs,
            settings=settings,
            kind=kind,
            schema_version=schema_version,
        )


@dataclass(frozen=True, slots=True)
class StageStatus:
    stage: str
    state: str
    reason: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "state": self.state,
            "reason": self.reason,
            **dict(self.details),
        }


__all__ = [
    "DocumentIndex",
    "DocumentScope",
    "PlanIndex",
    "PlanScope",
    "ProjectFormatError",
    "ProjectManifest",
    "StageStatus",
]
