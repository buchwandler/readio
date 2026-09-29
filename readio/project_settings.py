"""Access and mutation helpers for Readio project-local settings."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import replace
from typing import Any
from pathlib import Path

from .errors import ReadioError
from .project import canonical_json
from .project_model import ProjectFormatError, ProjectManifest


def project_ssmd_settings(manifest: ProjectManifest) -> dict[str, Any]:
    """Return a detached copy of the project SSMD settings object."""
    return dict(manifest.settings.get("ssmd", {}))


class ProjectVoiceProviderError(ReadioError):
    code = "readio.project_voice_provider_ambiguous"

    def __init__(self, providers: tuple[str, ...]) -> None:
        super().__init__(
            "Project has bindings for multiple voice providers but no active provider is set: "
            + ", ".join(providers)
        )
        self.details = {"providers": list(providers)}


def project_voice_provider(manifest: ProjectManifest) -> str | None:
    """Return the project-local active SSMD provider, if one is set."""
    return project_ssmd_settings(manifest).get("voice_provider")


def project_voice_binding_providers(
    manifest: ProjectManifest, *, non_empty_only: bool = True
) -> tuple[str, ...]:
    """Return provider namespaces with project-local role bindings."""
    bindings = project_ssmd_settings(manifest).get("voice_bindings", {})
    providers = (provider for provider, roles in bindings.items() if not non_empty_only or roles)
    return tuple(sorted(providers))


def with_project_voice_provider(manifest: ProjectManifest, provider: str) -> ProjectManifest:
    """Return a manifest with an active project-local SSMD provider."""
    _require_non_empty_string(provider, "provider")
    settings = dict(manifest.settings)
    ssmd = dict(settings.get("ssmd", {}))
    ssmd["voice_provider"] = provider
    settings["ssmd"] = ssmd
    return replace(manifest, settings=settings)


def resolve_project_voice_provider(
    manifest: ProjectManifest,
    cfg: Any,
    *,
    explicit_provider: str | None = None,
    explicit_engine: str | None = None,
) -> str:
    """Resolve the effective SSMD provider for project work."""
    if explicit_provider is not None:
        _require_non_empty_string(explicit_provider, "provider")
        return explicit_provider

    if explicit_engine is not None:
        from .engines.registry import ssmd_provider_for_engine

        provider = ssmd_provider_for_engine(explicit_engine)
        if provider is not None:
            return provider

    active_provider = project_voice_provider(manifest)
    if active_provider is not None:
        return active_provider

    candidates = project_voice_binding_providers(manifest)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise ProjectVoiceProviderError(candidates)
    return cfg.ssmd.voice_provider


def project_voice_bindings(manifest: ProjectManifest, provider: str) -> dict[str, str]:
    """Return project-local role bindings for one provider."""
    settings = project_ssmd_settings(manifest)
    bindings = settings.get("voice_bindings", {})
    return dict(bindings.get(provider, {}))


def project_voice_bindings_provenance(provider: str, bindings: Mapping[str, str]) -> dict[str, Any]:
    """Return stable, non-identity provenance for project voice settings."""
    normalized = dict(sorted(bindings.items()))
    identity = {
        "schema": "readio.project-voice-bindings.v1",
        "provider": provider,
        "bindings": normalized,
    }
    digest = hashlib.sha256(canonical_json(identity)).hexdigest()
    return {
        "provider": provider,
        "bindings": normalized,
        "sha256": f"sha256:{digest}",
    }


def with_project_voice_binding(
    manifest: ProjectManifest,
    *,
    provider: str,
    role: str,
    voice: str,
) -> ProjectManifest:
    """Return a manifest with one project-local voice binding changed."""
    _require_non_empty_string(provider, "provider")
    _require_non_empty_string(role, "role")
    _require_non_empty_string(voice, "voice")

    settings = dict(manifest.settings)
    ssmd = dict(settings.get("ssmd", {}))
    all_bindings = dict(ssmd.get("voice_bindings", {}))
    provider_bindings = dict(all_bindings.get(provider, {}))
    provider_bindings[role] = voice
    all_bindings[provider] = provider_bindings
    ssmd["voice_bindings"] = all_bindings
    settings["ssmd"] = ssmd
    return replace(manifest, settings=settings)


def without_project_voice_binding(
    manifest: ProjectManifest,
    *,
    provider: str,
    role: str,
) -> ProjectManifest:
    """Return a manifest with only the selected project-local binding removed."""
    _require_non_empty_string(provider, "provider")
    _require_non_empty_string(role, "role")

    settings = dict(manifest.settings)
    ssmd = dict(settings.get("ssmd", {}))
    all_bindings = dict(ssmd.get("voice_bindings", {}))
    provider_bindings = dict(all_bindings.get(provider, {}))
    provider_bindings.pop(role, None)
    if provider_bindings:
        all_bindings[provider] = provider_bindings
    else:
        all_bindings.pop(provider, None)
    if all_bindings:
        ssmd["voice_bindings"] = all_bindings
    else:
        ssmd.pop("voice_bindings", None)
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
    from .api.types import ProjectSettings, UNSET

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
    "apply_project_settings_patch",
    "merge_project_synthesis_request",
    "project_pipeline_settings",
    "project_settings_fingerprint",
    "project_planning_config",
    "project_planning_settings_fingerprint",
    "synthesis_request_fingerprint",
    "project_settings_from_manifest",
    "project_settings_to_dict",
    "project_synthesis_request",
    "with_project_settings",
    "ProjectVoiceProviderError",
    "project_ssmd_settings",
    "project_voice_binding_providers",
    "project_voice_bindings",
    "project_voice_bindings_provenance",
    "project_voice_provider",
    "resolve_project_voice_provider",
    "with_project_voice_binding",
    "with_project_voice_provider",
    "without_project_voice_binding",
]
