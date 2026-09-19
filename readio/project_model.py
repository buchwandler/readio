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

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "kind": self.kind, "path": self.path}
        for key, value in (
            ("title", self.title),
            ("plan_id", self.plan_id),
            ("sha256", self.sha256),
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
    plan_index_path: str = "plan/index.json"
    synthesis_profile_path: str = "synthesis/profile.json"
    synthesis_trace_path: str = "synthesis/trace.json"
    composition_audiojob_path: str = "composition/audiojob.json"
    composition_state_path: str = "composition/state.json"
    composition_master_path: str = "composition/master.wav"
    composition_timeline_path: str = "composition/timeline.json"
    outputs: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    settings: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = 1
    format: str = "readio.project"

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "name": self.name,
            "source": {
                "path": self.source_path,
                "format": self.source_format,
                "sha256": self.source_sha256,
            },
            "document": {
                "metadata_path": self.document_metadata_path,
                "text_path": self.document_text_path,
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

    @classmethod
    def from_dict(cls, value: Any) -> ProjectManifest:
        data = _require_mapping(value, "project manifest")
        if data.get("format") != "readio.project":
            raise ProjectFormatError("project.json has an unexpected format")
        if data.get("schema_version") != 1:
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
        return cls(
            project_id=_require_string(data.get("project_id"), "project_id"),
            name=_require_string(data.get("name"), "name"),
            source_path=_require_string(source.get("path"), "source.path"),
            source_format=_require_string(source.get("format"), "source.format"),
            source_sha256=_require_string(source.get("sha256"), "source.sha256"),
            document_metadata_path=_require_string(
                document.get("metadata_path"), "document.metadata_path"
            ),
            document_text_path=_require_string(document.get("text_path"), "document.text_path"),
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


__all__ = ["PlanIndex", "PlanScope", "ProjectFormatError", "ProjectManifest", "StageStatus"]
