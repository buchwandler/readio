"""Access and mutation helpers for Readio project-local settings."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from .engines.registry import normalize_engine_id
from .errors import ReadioError
from .project import canonical_json
from .project_model import ProjectFormatError, ProjectManifest
from .role_targets import VoiceTarget, ssmd_namespace_for_engine, voice_target_from_mapping


def project_ssmd_settings(manifest: ProjectManifest) -> dict[str, Any]:
    """Return a detached copy of the project SSMD settings object."""
    return dict(manifest.settings.get("ssmd", {}))


class ProjectVoiceNamespaceError(ReadioError):
    code = "readio.project_voice_namespace_ambiguous"

    def __init__(self, namespaces: tuple[str, ...]) -> None:
        super().__init__(
            "Project has role bindings in multiple SSMD namespaces but no engine was selected: "
            + ", ".join(namespaces)
        )
        self.details = {"namespaces": list(namespaces)}


def project_voice_namespace(manifest: ProjectManifest) -> str | None:
    """Return one unambiguous SSMD namespace implied by structured project roles."""
    namespaces = project_voice_binding_namespaces(manifest)
    return namespaces[0] if len(namespaces) == 1 else None


def project_voice_binding_namespaces(manifest: ProjectManifest) -> tuple[str, ...]:
    namespaces = {
        ssmd_namespace_for_engine(target.engine)
        for target in project_role_bindings(manifest).values()
    }
    return tuple(sorted(namespaces))


def resolve_project_voice_namespace(
    manifest: ProjectManifest,
    cfg: Any,
    *,
    explicit_engine: str | None = None,
) -> str:
    """Resolve the SSMD namespace from an explicit engine or structured project roles."""
    if explicit_engine is not None:
        _require_non_empty_string(explicit_engine, "engine")
        return ssmd_namespace_for_engine(explicit_engine)

    namespaces = project_voice_binding_namespaces(manifest)
    if len(namespaces) == 1:
        return namespaces[0]
    if len(namespaces) > 1:
        raise ProjectVoiceNamespaceError(namespaces)
    return ssmd_namespace_for_engine(cfg.reader.engine)


def project_role_bindings(manifest: ProjectManifest) -> dict[str, VoiceTarget]:
    """Return role-centric project targets, normalized to canonical engine IDs."""
    values = project_ssmd_settings(manifest).get("role_bindings", {})
    if not isinstance(values, Mapping):
        raise ProjectFormatError("project.settings.ssmd.role_bindings must be a mapping")
    return {
        role: voice_target_from_mapping(value, name=f"project role binding {role!r}")
        for role, value in values.items()
    }


def effective_project_role_targets(
    manifest: ProjectManifest,
) -> tuple[dict[str, VoiceTarget], dict[str, tuple[str, ...]]]:
    """Return only schema-3 structured role targets; legacy ambiguity belongs to migration."""
    return project_role_bindings(manifest), {}


def project_role_targets_provenance(manifest: ProjectManifest) -> dict[str, Any]:
    """Return a deterministic fingerprint for effective role targets and conflicts."""
    targets, ambiguities = effective_project_role_targets(manifest)
    bindings = {role: target.to_dict() for role, target in sorted(targets.items())}
    conflicts = {role: list(namespaces) for role, namespaces in sorted(ambiguities.items())}
    value = {"bindings": bindings, "ambiguities": conflicts}
    return {**value, "sha256": hashlib.sha256(canonical_json(value)).hexdigest()}


def with_project_role_binding(
    manifest: ProjectManifest, *, role: str, target: VoiceTarget
) -> ProjectManifest:
    """Persist one engine-qualified role target."""
    _require_non_empty_string(role, "role")
    if not isinstance(target, VoiceTarget):
        raise TypeError("target must be a VoiceTarget")
    settings = dict(manifest.settings)
    ssmd = dict(settings.get("ssmd", {}))
    role_bindings = dict(ssmd.get("role_bindings", {}))
    role_bindings[role] = target.to_dict()
    ssmd["role_bindings"] = role_bindings
    settings["ssmd"] = ssmd
    return replace(manifest, settings=settings)


def without_project_role_binding(manifest: ProjectManifest, *, role: str) -> ProjectManifest:
    """Remove one role-centric project target while preserving other settings."""
    _require_non_empty_string(role, "role")
    settings = dict(manifest.settings)
    ssmd = dict(settings.get("ssmd", {}))
    role_bindings = dict(ssmd.get("role_bindings", {}))
    role_bindings.pop(role, None)
    if role_bindings:
        ssmd["role_bindings"] = role_bindings
    else:
        ssmd.pop("role_bindings", None)
    settings["ssmd"] = ssmd
    return replace(manifest, settings=settings)


def _require_non_empty_string(value: str, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ProjectFormatError(f"project voice binding {name} must be a non-empty string")


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    return value


def _stored_path(value: Path | None, project_root: Path) -> str | None:
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _loaded_path(value: str | None, project_root: Path) -> Path | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else project_root / path


def project_pipeline_settings(manifest: ProjectManifest) -> dict[str, dict[str, Any]]:
    """Return detached copies of the configured pipeline sections."""
    sections = ("synthesis", "composition", "export", "audiobook_export")
    return {
        section: _plain_json(manifest.settings[section])
        for section in sections
        if section in manifest.settings
    }


def project_settings_from_manifest(manifest: ProjectManifest, project_root: Path) -> Any:
    """Deserialize supported settings into detached immutable public values."""
    from .api.types import (
        AudiobookExportOptions,
        CompositionOptions,
        ExportOptions,
        ProjectSettings,
        ProjectSynthesisSettings,
    )

    raw = project_pipeline_settings(manifest)
    synthesis = None
    if "synthesis" in raw:
        values = dict(raw["synthesis"])
        for key in ("lexicons", "detect_languages"):
            if values.get(key) is not None:
                values[key] = tuple(values[key])
        values["engine_options"] = values.get("engine_options")
        values["voice_file"] = _loaded_path(values.get("voice_file"), project_root)
        synthesis = ProjectSynthesisSettings(
            **{
                key: value
                for key, value in values.items()
                if key in ProjectSynthesisSettings.__dataclass_fields__
            }
        )
    composition = None
    if "composition" in raw:
        values = raw["composition"]
        composition = CompositionOptions(
            mastering=values.get("mastering", "spoken-word"),
            target_lufs=values.get("target_lufs"),
            true_peak_ceiling_dbtp=values.get("true_peak_ceiling_dbtp"),
            peak_policy=values.get("peak_policy", "reduce_gain"),
            clip_policy=values.get("clip_policy", "clamp"),
            sample_rate=values.get("sample_rate"),
        )
    export = None
    if "export" in raw:
        values = raw["export"]
        export = ExportOptions(
            format=values.get("format", "wav"),
            output=_loaded_path(values.get("output"), project_root),
            bitrate=values.get("bitrate"),
        )
    audiobook_export = None
    if "audiobook_export" in raw:
        values = raw["audiobook_export"]
        audiobook_export = AudiobookExportOptions(
            format=values.get("format", "m4b"),
            output=_loaded_path(values.get("output"), project_root),
            title=values.get("title"),
            author=values.get("author"),
            cover=_loaded_path(values.get("cover"), project_root),
            bitrate=values.get("bitrate"),
        )
    return ProjectSettings(
        synthesis=synthesis,
        composition=composition,
        export=export,
        audiobook_export=audiobook_export,
    )


def project_settings_to_dict(settings: Any, project_root: Path) -> dict[str, Any]:
    """Serialize configured sections without invocation-only fields."""
    result: dict[str, Any] = {}
    synthesis = settings.synthesis
    if synthesis is not None:
        synthesis_values: dict[str, Any] = {}
        for name in synthesis.__dataclass_fields__:
            value = getattr(synthesis, name)
            if value is None:
                continue
            if name == "voice_file":
                value = _stored_path(value, project_root)
            elif name == "engine":
                value = normalize_engine_id(value)
            elif name == "engine_options":
                value = _plain_json(value)
            elif isinstance(value, tuple):
                value = list(value)
            synthesis_values[name] = value
        result["synthesis"] = synthesis_values
    composition = settings.composition
    if composition is not None:
        result["composition"] = {
            "mastering": composition.mastering,
            "target_lufs": composition.target_lufs,
            "true_peak_ceiling_dbtp": composition.true_peak_ceiling_dbtp,
            "peak_policy": composition.peak_policy,
            "clip_policy": composition.clip_policy,
            "sample_rate": composition.sample_rate,
        }
    export = settings.export
    if export is not None:
        result["export"] = {
            "format": export.format,
            "output": _stored_path(export.output, project_root),
            "bitrate": export.bitrate,
        }
    audiobook_export = settings.audiobook_export
    if audiobook_export is not None:
        result["audiobook_export"] = {
            "format": audiobook_export.format,
            "output": _stored_path(audiobook_export.output, project_root),
            "title": audiobook_export.title,
            "author": audiobook_export.author,
            "cover": _stored_path(audiobook_export.cover, project_root),
            "bitrate": audiobook_export.bitrate,
        }
    return result


def with_project_settings(
    manifest: ProjectManifest, settings: Any, project_root: Path
) -> ProjectManifest:
    """Replace supported pipeline sections while preserving other namespaces."""
    supported = {"synthesis", "composition", "export", "audiobook_export"}
    raw_settings = dict(manifest.settings)
    for section in supported:
        raw_settings.pop(section, None)
    raw_settings.update(project_settings_to_dict(settings, project_root))
    return replace(manifest, settings=raw_settings)


def apply_project_settings_patch(
    manifest: ProjectManifest, patch: Any, project_root: Path
) -> ProjectManifest:
    """Apply a section patch, distinguishing UNSET from an explicit clear."""
    from .api.types import UNSET, ProjectSettings

    current = project_settings_from_manifest(manifest, project_root)
    values = {
        name: getattr(current, name)
        for name in ("synthesis", "composition", "export", "audiobook_export")
    }
    for name in values:
        value = getattr(patch, name)
        if value is not UNSET:
            values[name] = value
    return with_project_settings(manifest, ProjectSettings(**values), project_root)


def project_settings_fingerprint(
    settings: Any, project_root: Path, section: str | None = None
) -> str:
    payload = project_settings_to_dict(settings, project_root)
    if section is not None:
        payload = {section: payload[section]} if section in payload else {}
    digest = hashlib.sha256(canonical_json(payload)).hexdigest()
    return f"sha256:{digest}"


def synthesis_request_fingerprint(request: Any, project_root: Path) -> str:
    """Fingerprint effective synthesis settings, excluding invocation-only refresh."""
    from .plan import SynthesisRequest

    if not isinstance(request, SynthesisRequest):
        raise TypeError("expected a SynthesisRequest")
    values: dict[str, Any] = {}
    for name in request.__dataclass_fields__:
        if name == "refresh":
            continue
        value = getattr(request, name)
        if name == "voice_file":
            value = _stored_path(value, project_root)
        values[name] = _plain_json(value)
    payload = {"schema": "readio.project-synthesis-request.v1", "settings": values}
    digest = hashlib.sha256(canonical_json(payload)).hexdigest()
    return f"sha256:{digest}"


def project_planning_settings_fingerprint(synthesis_settings: Any) -> str | None:
    """Fingerprint only synthesis preferences that affect the semantic plan."""
    if synthesis_settings is None:
        return None
    fields = (
        "language",
        "unit",
        "spacy",
        "pause_mode",
        "language_detection",
        "detect_languages",
    )
    values = {
        name: _plain_json(getattr(synthesis_settings, name))
        for name in fields
        if getattr(synthesis_settings, name) is not None
    }
    if not values:
        return None
    payload = {"schema": "readio.project-planning-settings.v1", "settings": values}
    digest = hashlib.sha256(canonical_json(payload)).hexdigest()
    return f"sha256:{digest}"


def project_planning_config(cfg: Any, synthesis_settings: Any) -> Any:
    """Apply saved planning-related synthesis preferences over global config."""
    if synthesis_settings is None:
        return cfg
    fields = (
        "language",
        "unit",
        "spacy",
        "pause_mode",
        "language_detection",
        "detect_languages",
    )
    reader_names = {"language": "lang"}
    values = {
        reader_names.get(name, name): getattr(synthesis_settings, name)
        for name in fields
        if getattr(synthesis_settings, name) is not None
    }
    if not values:
        return cfg
    return replace(cfg, reader=replace(cfg.reader, **values))


def project_synthesis_request(settings: Any) -> Any:
    """Convert sparse persisted synthesis preferences to a request value."""
    from .plan import SynthesisRequest

    if settings is None:
        return SynthesisRequest()
    values = {
        name: getattr(settings, name)
        for name in settings.__dataclass_fields__
        if getattr(settings, name) is not None
    }
    if "engine_options" in values:
        values["engine_options"] = _plain_json(values["engine_options"])
    values["clear_lexicons"] = bool(settings.clear_lexicons)
    values["auto_lexicons"] = bool(settings.auto_lexicons)
    values["allow_experimental"] = bool(settings.allow_experimental)
    values["offline"] = bool(settings.offline)
    return SynthesisRequest(**values)


def merge_project_synthesis_request(project_settings: Any, invocation_request: Any = None) -> Any:
    """Merge sparse invocation choices over durable project synthesis settings."""
    from .plan import SynthesisRequest

    base = project_synthesis_request(project_settings)
    if invocation_request is None:
        return base
    scalar_fields = (
        "language",
        "model",
        "model_source",
        "quality",
        "voice",
        "speaker",
        "spacy",
        "short_sentence",
        "g2p_fallback",
        "lexicon_data_policy",
        "language_detection",
        "detect_languages",
        "speed",
        "voice_level",
        "pause_mode",
        "unit",
        "engine",
        "voice_file",
    )
    values = {
        name: getattr(invocation_request, name)
        if getattr(invocation_request, name) is not None
        else getattr(base, name)
        for name in scalar_fields
    }
    invocation_has_lexicon_mode = (
        invocation_request.lexicons is not None
        or invocation_request.clear_lexicons
        or invocation_request.auto_lexicons
    )
    if invocation_has_lexicon_mode:
        values["lexicons"] = invocation_request.lexicons
        values["clear_lexicons"] = invocation_request.clear_lexicons
        values["auto_lexicons"] = invocation_request.auto_lexicons
    else:
        values["lexicons"] = base.lexicons
        values["clear_lexicons"] = base.clear_lexicons
        values["auto_lexicons"] = base.auto_lexicons
    values["allow_experimental"] = invocation_request.allow_experimental or base.allow_experimental
    values["offline"] = invocation_request.offline or base.offline
    values["refresh"] = invocation_request.refresh
    values["engine_options"] = {
        **dict(base.engine_options),
        **_plain_json(invocation_request.engine_options),
    }
    return SynthesisRequest(**values)


__all__ = [
    "ProjectVoiceNamespaceError",
    "apply_project_settings_patch",
    "effective_project_role_targets",
    "merge_project_synthesis_request",
    "project_pipeline_settings",
    "project_planning_config",
    "project_planning_settings_fingerprint",
    "project_role_bindings",
    "project_role_targets_provenance",
    "project_settings_fingerprint",
    "project_settings_from_manifest",
    "project_settings_to_dict",
    "project_ssmd_settings",
    "project_synthesis_request",
    "project_voice_binding_namespaces",
    "project_voice_namespace",
    "resolve_project_voice_namespace",
    "synthesis_request_fingerprint",
    "with_project_role_binding",
    "with_project_settings",
    "without_project_role_binding",
]
