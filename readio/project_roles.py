"""Inspect logical SSMD roles and their effective project voice bindings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import ssmd as ssmd_api

from .config import ReadioConfig
from .engines.registry import ssmd_provider_for_engine
from .errors import ReadioError
from .models import ModelDiscoveryError
from .project import Project, update_project_manifest
from .project_settings import (
    project_voice_bindings,
    resolve_project_voice_provider,
    with_project_voice_binding,
    with_project_voice_provider,
    without_project_voice_binding,
)
from .ssmd import document_voice_bindings, resolve_voice_references
from .voices import resolve_voice_selector


class ProjectRoleError(ReadioError):
    """A project role operation failed with a stable machine-readable code."""

    def __init__(self, message: str, *, code: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}

@dataclass(frozen=True, slots=True)
class RoleLocation:
    scope_id: str
    lines: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"scope_id": self.scope_id, "lines": list(self.lines)}


@dataclass(frozen=True, slots=True)
class ProjectRole:
    role: str
    uses: int
    locations: tuple[RoleLocation, ...]
    document_bindings: dict[str, str]
    project_binding: str | None
    config_binding: str | None
    effective_voice: str | None
    origin: str
    status: str
    effective_by_scope: dict[str, dict[str, str | None]]

    @property
    def scope_count(self) -> int:
        return len(self.locations)

    @property
    def document_binding(self) -> str | None:
        values = set(self.document_bindings.values())
        return next(iter(values)) if len(values) == 1 else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "uses": self.uses,
            "scope_count": self.scope_count,
            "effective_voice": self.effective_voice,
            "origin": self.origin,
            "status": self.status,
            "document_binding": self.document_binding,
            "document_bindings": dict(self.document_bindings),
            "project_binding": self.project_binding,
            "config_binding": self.config_binding,
            "effective_by_scope": {
                scope: dict(binding) for scope, binding in self.effective_by_scope.items()
            },
            "locations": [location.to_dict() for location in self.locations],
        }


@dataclass(frozen=True, slots=True)
class ProjectRoleInspection:
    provider: str
    roles: tuple[ProjectRole, ...]

    @property
    def unresolved(self) -> tuple[str, ...]:
        return tuple(role.role for role in self.roles if role.effective_voice is None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "roles": [role.to_dict() for role in self.roles],
            "unresolved": list(self.unresolved),
        }


def inspect_project_roles(
    project: Project,
    cfg: ReadioConfig,
    *,
    provider: str | None = None,
) -> ProjectRoleInspection:
    """Discover project SSMD roles and resolve their effective binding layers."""
    selected_provider = resolve_project_voice_provider(
        project.manifest, cfg, explicit_provider=provider
    )
    project_bindings = project_voice_bindings(project.manifest, selected_provider)
    config_provider = cfg.voices.get(selected_provider)
    configured_roles = dict(config_provider.roles) if config_provider is not None else {}
    aggregate: dict[str, dict[str, Any]] = {}

    for scope in project.document_scopes():
        if scope.input_format.casefold() != "ssmd":
            continue
        text = _scope_ssmd_text(project, scope)
        front_matter = ssmd_api.parse_front_matter(text)
        line_offset = text[: front_matter.source_end].count("\n") if front_matter.present else 0
        ssmd_api.parse_ssmd(text, strict_parse=True)
        references = ssmd_api.extract_voice_references(text)
        document_bindings = document_voice_bindings(text).get(selected_provider, {})
        resolutions = {
            item.reference: item
            for item in resolve_voice_references(
                text,
                cfg,
                project_bindings=project_bindings,
                provider=selected_provider,
            )
        }
        for use in references:
            role = aggregate.setdefault(
                use.reference,
                {
                    "uses": 0,
                    "locations": [],
                    "document_bindings": {},
                    "effective_by_scope": {},
                    "resolutions": [],
                },
            )
            role["uses"] += use.count
            role["locations"].append(
                RoleLocation(scope.id, tuple(line + line_offset for line in use.lines))
            )
            if use.reference in document_bindings:
                role["document_bindings"][scope.id] = document_bindings[use.reference]
            resolved = resolutions[use.reference]
            role["resolutions"].append((resolved.voice, resolved.origin))
            role["effective_by_scope"][scope.id] = {
                "voice": resolved.voice,
                "origin": resolved.origin,
                "document_binding": document_bindings.get(use.reference),
            }

    roles = []
    for reference, value in sorted(aggregate.items()):
        resolutions = set(value["resolutions"])
        if len(resolutions) > 1:
            effective_voice = None
            origin = "mixed"
            status = "mixed"
        else:
            effective_voice, origin = next(iter(resolutions))
            if effective_voice is None:
                origin = "unresolved"
                status = "unresolved"
            else:
                status = "resolved"
        roles.append(
            ProjectRole(
                role=reference,
                uses=value["uses"],
                locations=tuple(value["locations"]),
                document_bindings=dict(value["document_bindings"]),
                project_binding=project_bindings.get(reference),
                config_binding=configured_roles.get(reference),
                effective_voice=effective_voice,
                origin=origin or "unresolved",
                status=status,
                effective_by_scope=dict(value["effective_by_scope"]),
            )
        )
    return ProjectRoleInspection(selected_provider, tuple(roles))


def bind_project_role(
    project: Project,
    cfg: ReadioConfig,
    role: str,
    voice: str,
    *,
    provider: str | None = None,
    offline: bool = False,
    refresh: bool = False,
 ) -> dict[str, Any]:
    """Persist a concrete voice for one SSMD role in this project."""
    role = role.strip()
    requested_voice = voice.strip()
    if not role:
        raise ProjectRoleError(
            "Role must be a non-empty string.",
            code="readio.project_role.unknown",
            details={"role": role, "available_roles": []},
        )
    if not requested_voice:
        raise ProjectRoleError(
            "Voice must be a non-empty string.",
            code="readio.project_role.voice_selector_invalid",
            details={"requested_voice": voice},
        )
    if provider is not None and not provider.strip():
        raise ProjectRoleError(
            "Provider must be a non-empty string.",
            code="readio.project_role.provider_mismatch",
            details={"provider": provider},
        )
    try:
        selection = resolve_voice_selector(
            requested_voice,
            language=None,
            model=None,
            source=None,
            offline=offline,
            refresh=refresh,
        )
    except ModelDiscoveryError as exc:
        if not exc.code.startswith("readio.voice_selector"):
            raise
        raise ProjectRoleError(
            str(exc),
            code="readio.project_role.voice_selector_invalid",
            details={"requested_voice": requested_voice},
        ) from exc
    assert selection is not None
    selector_provider = (
        ssmd_provider_for_engine(selection.engine)
        if selection.selector is not None and selection.engine is not None
        else None
    )
    if provider is not None and selector_provider is not None and provider != selector_provider:
        raise ProjectRoleError(
            f"Voice selector {requested_voice!r} belongs to provider {selector_provider!r}, "
            f"not requested provider {provider!r}.",
            code="readio.project_role.provider_mismatch",
            details={
                "provider": provider,
                "selector_provider": selector_provider,
                "requested_voice": requested_voice,
            },
        )
    selected_provider = provider or selector_provider or resolve_project_voice_provider(
        project.manifest, cfg
    )
    stored_voice = selection.voice if selection.selector is not None else requested_voice

    inspection = inspect_project_roles(project, cfg, provider=selected_provider)
    if role not in {item.role for item in inspection.roles}:
        raise ProjectRoleError(
            f"Unknown SSMD role {role!r}.",
            code="readio.project_role.unknown",
            details={
                "role": role,
                "available_roles": [item.role for item in inspection.roles],
            },
        )
    selected_role = next(item for item in inspection.roles if item.role == role)
    if selected_role.document_bindings:
        bindings = selected_role.document_bindings
        document_voice = next(iter(set(bindings.values()))) if len(set(bindings.values())) == 1 else "mixed"
        scopes = list(bindings)
        raise ProjectRoleError(
            f"Role {role!r} is bound by the SSMD document to {document_voice!r}. "
            "Document bindings are authoritative. Change the source binding, or use "
            "readio ssmd bind to create a new bound SSMD file.",
            code="readio.project_role.document_bound",
            details={
                "role": role,
                "provider": selected_provider,
                "document_voice": document_voice,
                "scopes": scopes,
                "document_bindings": dict(bindings),
            },
        )

    updated = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            with_project_voice_provider(manifest, selected_provider),
            provider=selected_provider,
            role=role,
            voice=stored_voice,
        ),
        operation=f"plan-bind-{role}",
    )
    effective = next(
        item for item in inspect_project_roles(updated, cfg, provider=selected_provider).roles
        if item.role == role
    )
    return {
        "ok": True,
        "project": str(updated.root),
        "provider": selected_provider,
        "role": role,
        "requested_voice": voice,
        "stored_voice": stored_voice,
        "effective_voice": effective.effective_voice,
        "origin": effective.origin,
        "semantic_plan_unchanged": True,
    }


def unbind_project_role(
    project: Project,
    cfg: ReadioConfig,
    role: str,
    *,
    provider: str | None = None,
 ) -> dict[str, Any]:
    """Remove only one project-local binding and report the newly exposed value."""
    selected_provider = resolve_project_voice_provider(
        project.manifest, cfg, explicit_provider=provider
    )
    if not role.strip():
        raise ProjectRoleError(
            "Role must be a non-empty string.",
            code="readio.project_role.binding_missing",
            details={"role": role, "provider": selected_provider},
        )
    role = role.strip()
    bindings = project_voice_bindings(project.manifest, selected_provider)
    if role not in bindings:
        raise ProjectRoleError(
            f"No project-local binding exists for role {role!r} and provider "
            f"{selected_provider!r}.",
            code="readio.project_role.binding_missing",
            details={"role": role, "provider": selected_provider},
        )
    removed_voice = bindings[role]
    updated = update_project_manifest(
        project,
        lambda manifest: without_project_voice_binding(
            manifest, provider=selected_provider, role=role
        ),
        operation=f"plan-unbind-{role}",
    )
    inspection = inspect_project_roles(updated, cfg, provider=selected_provider)
    effective = next((item for item in inspection.roles if item.role == role), None)
    return {
        "ok": True,
        "project": str(updated.root),
        "provider": selected_provider,
        "role": role,
        "removed_voice": removed_voice,
        "effective_voice": effective.effective_voice if effective else None,
        "origin": effective.origin if effective else "unresolved",
    }





def _scope_ssmd_text(project: Project, scope: Any) -> str:
    if (
        project.manifest.kind == "document"
        and scope.id == "document"
        and project.manifest.source_format.casefold() == "ssmd"
):
        return project.paths["source"].read_text(encoding="utf-8")
    return project.load_document_scope(scope).text


__all__ = [
    "ProjectRole",
    "ProjectRoleInspection",
    "RoleLocation",
    "inspect_project_roles",
]
