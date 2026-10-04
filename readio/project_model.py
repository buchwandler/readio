"""Serializable models for persistent Readio projects.

The project format deliberately keeps semantic, acoustic, composition, and
encoding identities separate.  Loaders are strict about the format and schema
markers so corrupted or unrelated directories fail early.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .config import (
    G2P_FALLBACKS,
    LANGUAGE_DETECTION_MODES,
    LEXICON_DATA_POLICIES,
    SHORT_SENTENCE_POLICIES,
    SPACY_LEGACY_ALIASES,
    SPACY_POLICIES,
    VOICE_LEVEL_MODES,
)
from .engines.registry import normalize_engine_id
from .formats import SUPPORTED_AUDIO_FORMATS


class ProjectFormatError(ValueError):
    """Raised when a persisted project artifact is malformed."""


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectFormatError(f"{name} must be an object")
    return value


def _validate_json_value(value: Any, name: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ProjectFormatError(f"{name} must contain only finite JSON numbers")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{name}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProjectFormatError(f"{name} object keys must be strings")
            _validate_json_value(item, f"{name}.{key}")
        return
    raise ProjectFormatError(f"{name} must contain JSON-compatible values")


def _require_optional_string(data: Mapping[str, Any], key: str, name: str) -> None:
    if key in data and data[key] is not None:
        _require_string(data[key], name)


def _require_bool(data: Mapping[str, Any], key: str, name: str) -> None:
    if key in data and not isinstance(data[key], bool):
        raise ProjectFormatError(f"{name} must be a boolean")


def _require_optional_path(data: Mapping[str, Any], key: str, name: str) -> None:
    _require_optional_string(data, key, name)


def _validate_synthesis_settings(value: Any) -> None:
    name = "project.settings.synthesis"
    synthesis = _require_mapping(value, name)
    if "refresh" in synthesis:
        raise ProjectFormatError(f"{name}.refresh is invocation-only and cannot be persisted")
    for key in (
        "language",
        "model",
        "model_source",
        "quality",
        "voice",
        "spacy",
        "voice_prompt",
        "short_sentence",
        "g2p_fallback",
        "lexicon_data_policy",
        "language_detection",
        "voice_level",
        "pause_mode",
        "unit",
        "engine",
    ):
        _require_optional_string(synthesis, key, f"{name}.{key}")
    engine = synthesis.get("engine")
    if engine is not None and (
        not isinstance(engine, str) or not engine or engine != normalize_engine_id(engine)
    ):
        raise ProjectFormatError(f"{name}.engine must use a canonical engine ID")
    if "speaker" in synthesis:
        speaker = synthesis["speaker"]
        if speaker is not None and (
            not isinstance(speaker, (str, int)) or isinstance(speaker, bool) or speaker == ""
        ):
            raise ProjectFormatError(f"{name}.speaker must be a non-empty string, integer, or None")
    for key in ("clear_lexicons", "auto_lexicons", "allow_experimental", "offline"):
        _require_bool(synthesis, key, f"{name}.{key}")
    lexicons = synthesis.get("lexicons")
    if lexicons is not None and (
        not isinstance(lexicons, list)
        or any(not isinstance(item, str) or not item for item in lexicons)
    ):
        raise ProjectFormatError(f"{name}.lexicons must be a list of non-empty strings or None")
    languages = synthesis.get("detect_languages")
    if languages is not None and (
        not isinstance(languages, list)
        or any(not isinstance(item, str) or not item for item in languages)
    ):
        raise ProjectFormatError(
            f"{name}.detect_languages must be a list of non-empty strings or None"
        )
    speed = synthesis.get("speed")
    if speed is not None and (
        isinstance(speed, bool)
        or not isinstance(speed, (int, float))
        or not math.isfinite(float(speed))
        or speed <= 0
    ):
        raise ProjectFormatError(f"{name}.speed must be a finite number greater than zero or None")
    spacy = synthesis.get("spacy")
    if spacy is not None and spacy not in {*SPACY_POLICIES, *SPACY_LEGACY_ALIASES}:
        raise ProjectFormatError(f"{name}.spacy has an unsupported policy")
    if synthesis.get("short_sentence") is not None and (
        synthesis["short_sentence"] not in SHORT_SENTENCE_POLICIES
    ):
        raise ProjectFormatError(f"{name}.short_sentence has an unsupported policy")
    if synthesis.get("g2p_fallback") is not None and synthesis["g2p_fallback"] not in G2P_FALLBACKS:
        raise ProjectFormatError(f"{name}.g2p_fallback has an unsupported policy")
    if (
        synthesis.get("lexicon_data_policy") is not None
        and synthesis["lexicon_data_policy"] not in LEXICON_DATA_POLICIES
    ):
        raise ProjectFormatError(f"{name}.lexicon_data_policy has an unsupported policy")
    if (
        synthesis.get("voice_level") is not None
        and synthesis["voice_level"] not in VOICE_LEVEL_MODES
    ):
        raise ProjectFormatError(f"{name}.voice_level has an unsupported policy")
    if synthesis.get("pause_mode") is not None and synthesis["pause_mode"] not in {
        "auto",
        "manual",
        "tts",
    }:
        raise ProjectFormatError(f"{name}.pause_mode has an unsupported policy")
    if synthesis.get("unit") is not None and synthesis["unit"] not in {"sentence", "paragraph"}:
        raise ProjectFormatError(f"{name}.unit has an unsupported value")
    if synthesis.get("language_detection") is not None and (
        synthesis["language_detection"] not in LANGUAGE_DETECTION_MODES
    ):
        raise ProjectFormatError(f"{name}.language_detection has an unsupported policy")
    if synthesis.get("clear_lexicons") and synthesis.get("auto_lexicons"):
        raise ProjectFormatError(f"{name} lexicon modes are mutually exclusive")
    if lexicons is not None and (synthesis.get("clear_lexicons") or synthesis.get("auto_lexicons")):
        raise ProjectFormatError(f"{name} lexicon modes are mutually exclusive")
    if "engine_options" in synthesis:
        engine_options = _require_mapping(synthesis["engine_options"], f"{name}.engine_options")
        _validate_json_value(engine_options, f"{name}.engine_options")
    _require_optional_path(synthesis, "voice_file", f"{name}.voice_file")
    selectors = tuple(
        key for key in ("voice", "voice_file", "voice_prompt") if synthesis.get(key) is not None
    )
    if len(selectors) > 1:
        raise ProjectFormatError(
            f"{name} voice, voice_file, and voice_prompt are mutually exclusive"
        )


def _validate_composition_settings(value: Any) -> None:
    name = "project.settings.composition"
    composition = _require_mapping(value, name)
    mastering = composition.get("mastering", "spoken-word")
    if not isinstance(mastering, str) or mastering not in {
        "spoken-word",
        "spoken-word-dual-mono",
        "broadcast-ebu",
        "peak-safe",
        "off",
    }:
        raise ProjectFormatError(f"{name}.mastering has an unsupported profile")
    for key in ("target_lufs", "true_peak_ceiling_dbtp"):
        number = composition.get(key)
        if number is not None and (
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not math.isfinite(float(number))
        ):
            raise ProjectFormatError(f"{name}.{key} must be a finite number or None")
    peak_policy = composition.get("peak_policy", "reduce_gain")
    if not isinstance(peak_policy, str) or peak_policy not in {"reduce_gain", "error"}:
        raise ProjectFormatError(f"{name}.peak_policy has an unsupported value")
    clip_policy = composition.get("clip_policy", "clamp")
    if not isinstance(clip_policy, str) or clip_policy not in {"clamp", "warn", "error"}:
        raise ProjectFormatError(f"{name}.clip_policy has an unsupported value")
    sample_rate = composition.get("sample_rate")
    if sample_rate is not None and (
        not isinstance(sample_rate, int) or isinstance(sample_rate, bool) or sample_rate <= 0
    ):
        raise ProjectFormatError(f"{name}.sample_rate must be a positive integer or None")


def _validate_export_settings(value: Any, *, audiobook: bool = False) -> None:
    section = "audiobook_export" if audiobook else "export"
    name = f"project.settings.{section}"
    options = _require_mapping(value, name)
    if "force" in options:
        raise ProjectFormatError(f"{name}.force is invocation-only and cannot be persisted")
    audio_format = options.get("format", "m4b" if audiobook else "wav")
    supported = {"m4b"} if audiobook else set(SUPPORTED_AUDIO_FORMATS)
    if not isinstance(audio_format, str) or audio_format not in supported:
        raise ProjectFormatError(f"{name}.format has an unsupported value")
    _require_optional_path(options, "output", f"{name}.output")
    _require_optional_string(options, "bitrate", f"{name}.bitrate")
    if audiobook:
        _require_optional_string(options, "title", f"{name}.title")
        _require_optional_string(options, "author", f"{name}.author")
        _require_optional_path(options, "cover", f"{name}.cover")


def _validate_project_settings(value: Any) -> None:
    settings = _require_mapping(value, "project.settings")
    if "ssmd" in settings:
        ssmd = _require_mapping(settings["ssmd"], "project.settings.ssmd")
        if "voice_provider" in ssmd or "voice_bindings" in ssmd:
            raise ProjectFormatError(
                "legacy project voice settings require `readio project migrate` before use"
            )
        if "role_bindings" in ssmd:
            role_bindings = _require_mapping(
                ssmd["role_bindings"], "project.settings.ssmd.role_bindings"
            )
            for role, target in role_bindings.items():
                role_name = _require_string(role, "project.settings.ssmd.role_bindings role")
                target_values = _require_mapping(
                    target, f"project.settings.ssmd.role_bindings.{role_name}"
                )
                _require_string(
                    target_values.get("engine"),
                    f"project.settings.ssmd.role_bindings.{role_name}.engine",
                )
                engine = target_values.get("engine")
                if (
                    not isinstance(engine, str)
                    or not engine
                    or engine != normalize_engine_id(engine)
                ):
                    raise ProjectFormatError(
                        f"project.settings.ssmd.role_bindings.{role_name}.engine must use a canonical engine ID"
                    )
                _require_string(
                    target_values.get("voice"),
                    f"project.settings.ssmd.role_bindings.{role_name}.voice",
                )
                if "target_id" in target_values:
                    _require_string(
                        target_values["target_id"],
                        f"project.settings.ssmd.role_bindings.{role_name}.target_id",
                    )
    validators = {
        "synthesis": _validate_synthesis_settings,
        "composition": _validate_composition_settings,
        "export": _validate_export_settings,
        "audiobook_export": lambda section: _validate_export_settings(section, audiobook=True),
    }
    for section, validator in validators.items():
        if section in settings:
            validator(settings[section])


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
    source_parent_id: str | None = None

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
            ("source_parent_id", self.source_parent_id),
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
        for key in (
            "title",
            "source_id",
            "href",
            "parent_id",
            "source_parent_id",
            "extracted_sha256",
        ):
            item = data.get(key)
            if item is not None and not isinstance(item, str):
                raise ProjectFormatError(f"document scope {key} must be a string")
            optional_strings[key] = item
        return cls(
            id=_require_string(data.get("id"), "document scope.id"),
            kind=_require_string(data.get("kind"), "document scope.kind"),
            path=_require_string(data.get("path"), "document scope.path"),
            input_format=_require_string(data.get("input_format"), "document scope.input_format"),
            title=optional_strings["title"],
            source_number=optional_ints["source_number"],
            source_id=optional_strings["source_id"],
            href=optional_strings["href"],
            parent_id=optional_strings["parent_id"],
            level=optional_ints["level"],
            char_count=optional_ints["char_count"],
            extracted_sha256=optional_strings["extracted_sha256"],
            diagnostics=tuple(dict(item) for item in diagnostics),
            source_parent_id=optional_strings["source_parent_id"],
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
    project_planning_settings_sha256: str | None = None
    schema_version: int = 1
    format: str = "readio.plan-index"

    def to_dict(self) -> dict[str, Any]:
        result = {
            "format": self.format,
            "schema_version": self.schema_version,
            "scopes": [scope.to_dict() for scope in self.scopes],
        }
        if self.project_planning_settings_sha256 is not None:
            result["project_planning_settings_sha256"] = self.project_planning_settings_sha256
        return result

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
        settings_sha256 = data.get("project_planning_settings_sha256")
        if settings_sha256 is not None and (
            not isinstance(settings_sha256, str) or not settings_sha256.startswith("sha256:")
        ):
            raise ProjectFormatError(
                "plan index project_planning_settings_sha256 must be a sha256 fingerprint"
            )
        return cls(
            tuple(PlanScope.from_dict(item) for item in scopes),
            project_planning_settings_sha256=settings_sha256,
        )


@dataclass(frozen=True, slots=True)
class ProjectManifest:
    project_id: str
    name: str
    source_path: str
    source_format: str
    source_sha256: str
    document_metadata_path: str = "document/metadata.json"
    document_text_path: str = "document/document.ssmd.md"
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
    schema_version: int = 3
    format: str = "readio.project"
    layout_mode: str = "standalone"
    workspace_manifest_path: str | None = None
    workspace_manifest_sha256: str | None = None

    def __post_init__(self) -> None:
        _validate_project_settings(self.settings)
        if self.schema_version == 3:
            if (
                self.layout_mode != "standalone"
                or self.workspace_manifest_path is not None
                or self.workspace_manifest_sha256 is not None
            ):
                raise ProjectFormatError("schema_version 3 projects must use the standalone layout")
        elif self.schema_version == 4:
            if self.kind != "audiobook" or self.layout_mode != "attached-ssmdbook":
                raise ProjectFormatError("schema_version 4 requires an attached audiobook layout")
            if self.source_format != "ssmdbook":
                raise ProjectFormatError("attached audiobook workspace format must be ssmdbook")
            if not self.workspace_manifest_path or not self.workspace_manifest_sha256:
                raise ProjectFormatError(
                    "schema_version 4 requires a workspace manifest path and digest"
                )
            if self.source_path != self.workspace_manifest_path:
                raise ProjectFormatError(
                    "attached project source path must match its workspace manifest path"
                )
        else:
            raise ProjectFormatError("unsupported project schema_version")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "format": self.format,
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "name": self.name,
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
            "kind": self.kind,
            "document": {"index_path": self.document_index_path},
        }
        if self.schema_version == 3:
            result["source"] = {
                "path": self.source_path,
                "format": self.source_format,
                "sha256": self.source_sha256,
            }
        else:
            result["layout"] = {"mode": self.layout_mode}
            result["workspace"] = {
                "format": self.source_format,
                "manifest_path": self.workspace_manifest_path,
                "manifest_sha256": self.workspace_manifest_sha256,
            }
        return result

    @classmethod
    def from_dict(cls, value: Any) -> ProjectManifest:
        data = _require_mapping(value, "project manifest")
        if data.get("format") != "readio.project":
            raise ProjectFormatError("project.json has an unexpected format")
        schema_version = data.get("schema_version")
        if schema_version not in {3, 4}:
            raise ProjectFormatError(
                "unsupported project schema_version; run `readio project migrate` for v0.3 data"
            )
        if schema_version == 3:
            source = _require_mapping(data.get("source"), "project.source")
            source_path = _require_string(source.get("path"), "source.path")
            source_format = _require_string(source.get("format"), "source.format")
            source_sha256 = _require_string(source.get("sha256"), "source.sha256")
            layout_mode = "standalone"
            workspace_manifest_path = None
            workspace_manifest_sha256 = None
        else:
            layout = _require_mapping(data.get("layout"), "project.layout")
            if layout.get("mode") != "attached-ssmdbook":
                raise ProjectFormatError("schema_version 4 layout.mode must be attached-ssmdbook")
            workspace = _require_mapping(data.get("workspace"), "project.workspace")
            source_path = _require_string(workspace.get("manifest_path"), "workspace.manifest_path")
            source_format = _require_string(workspace.get("format"), "workspace.format")
            source_sha256 = _require_string(
                workspace.get("manifest_sha256"), "workspace.manifest_sha256"
            )
            layout_mode = "attached-ssmdbook"
            workspace_manifest_path = source_path
            workspace_manifest_sha256 = source_sha256
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
            source_path=source_path,
            source_format=source_format,
            source_sha256=source_sha256,
            document_metadata_path="document/metadata.json",
            document_text_path="document/document.ssmd.md",
            document_index_path=_require_string(document.get("index_path"), "document.index_path"),
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
            kind=_require_string(data.get("kind"), "kind"),
            schema_version=schema_version,
            layout_mode=layout_mode,
            workspace_manifest_path=workspace_manifest_path,
            workspace_manifest_sha256=workspace_manifest_sha256,
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
