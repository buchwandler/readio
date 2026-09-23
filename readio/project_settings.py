"""Access and mutation helpers for Readio project-local settings."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from .project import canonical_json
from .project_model import ProjectFormatError, ProjectManifest


def project_ssmd_settings(manifest: ProjectManifest) -> dict[str, Any]:
    """Return a detached copy of the project SSMD settings object."""
    return dict(manifest.settings.get("ssmd", {}))


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
    all_bindings[provider] = provider_bindings
    ssmd["voice_bindings"] = all_bindings
    settings["ssmd"] = ssmd
    return replace(manifest, settings=settings)


def _require_non_empty_string(value: str, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ProjectFormatError(f"project voice binding {name} must be a non-empty string")


__all__ = [
    "project_ssmd_settings",
    "project_voice_bindings",
    "project_voice_bindings_provenance",
    "with_project_voice_binding",
    "without_project_voice_binding",
]
