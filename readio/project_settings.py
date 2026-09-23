"""Access and mutation helpers for Readio project-local settings."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

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
    providers = (
        provider
        for provider, roles in bindings.items()
        if not non_empty_only or roles
    )
    return tuple(sorted(providers))


def with_project_voice_provider(
    manifest: ProjectManifest, provider: str
) -> ProjectManifest:
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


def project_voice_bindings_provenance(
    provider: str, bindings: Mapping[str, str]
) -> dict[str, Any]:
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


__all__ = [
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
