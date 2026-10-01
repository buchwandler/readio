"""Inspect logical SSMD roles and their effective project voice bindings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import ReadioConfig
from .engines.registry import engine_for_ssmd_provider, normalize_engine_id
from .errors import ReadioError
from .models import ModelDiscoveryError
from .project import Project, update_project_manifest
from .project_settings import (
    effective_project_role_targets,
    project_role_bindings,
    project_ssmd_settings,
    project_voice_bindings,
    project_voice_provider,
    resolve_project_voice_provider,
    with_project_role_binding,
    without_project_role_binding,
    without_project_voice_binding,
)
from .role_targets import VoiceTarget
from .ssmd import document_voice_bindings, parse_ssmd_09, resolve_voice_references
from .voice_refs import public_system_for_engine
from .voices import resolve_voice_reference


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "uses": self.uses,
            "scope_count": self.scope_count,
            "effective_voice": self.effective_voice,
            "project_target": self.project_target.to_dict() if self.project_target else None,
            "config_target": self.config_target.to_dict() if self.config_target else None,
            "effective_target": self.effective_target.to_dict() if self.effective_target else None,
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
    provider: str | None
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
    project_targets, project_ambiguities = effective_project_role_targets(project.manifest)
    active_provider = project_voice_provider(project.manifest)
    legacy_raw = project_ssmd_settings(project.manifest).get("voice_bindings", {})
    if active_provider is not None:
        provider_hint = active_provider
    elif len(legacy_raw) == 1:
        provider_hint = next(iter(legacy_raw))
    else:
        provider_hint = cfg.ssmd.voice_provider

    configured_targets = dict(cfg.roles)
    configured_candidates: dict[str, list[tuple[str, str]]] = {}
    for namespace, settings in cfg.voices.items():
        for role, voice in settings.roles.items():
            if role not in configured_targets:
                configured_candidates.setdefault(role, []).append((namespace, voice))
    for role, candidates in configured_candidates.items():
        if len(candidates) != 1:
            continue
        namespace, voice = candidates[0]
        try:
            configured_targets[role] = VoiceTarget(engine_for_ssmd_provider(namespace), voice)
        except ValueError:
            pass
    configured_roles = {role: target.voice for role, target in configured_targets.items()}
    aggregate: dict[str, dict[str, Any]] = {}

    for scope in project.document_scopes():
        if scope.input_format.casefold() != "ssmd":
            continue
        text = _scope_ssmd_text(project, scope)
        source_path = project.path(scope.path)
        parsed = parse_ssmd_09(text, source_path=source_path)
        references = parsed.voice_references
        all_document_bindings = document_voice_bindings(
            text, source_path=source_path, parsed=parsed
        )
        resolutions = {
            item.reference: item
            for item in resolve_voice_references(
                text,
                cfg,
                project_targets=project_targets,
                project_ambiguities=project_ambiguities,
                configured_targets=configured_targets,
                provider=provider_hint,
                source_path=source_path,
                parsed=parsed,
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
            role["locations"].append(RoleLocation(scope.id, use.lines))
            scope_document_bindings = [
                (namespace, bindings[use.reference])
                for namespace, bindings in all_document_bindings.items()
                if use.reference in bindings
            ]
            if len(scope_document_bindings) == 1:
                role["document_bindings"][scope.id] = scope_document_bindings[0][1]
            elif scope_document_bindings:
                role["document_bindings"][scope.id] = "ambiguous"
            resolved = resolutions[use.reference]
            role["resolutions"].append((resolved.target, resolved.origin))
            target = resolved.target
            role["effective_by_scope"][scope.id] = {
                "engine": target.engine if target else None,
                "provider": target.provider if target else None,
                "voice": target.voice if target else None,
                "target_id": target.target_id if target else None,
                "origin": resolved.origin,
                "document_binding": role["document_bindings"].get(scope.id),
            }
    roles = []
    for reference, value in sorted(aggregate.items()):
        resolutions = set(value["resolutions"])
        if len(resolutions) > 1:
            effective_target = None
            origin = "mixed"
            status = "mixed"
        else:
            effective_target, origin = next(iter(resolutions))
            if effective_target is None:
                origin = "unresolved"
                status = "unresolved"
            else:
                status = "resolved"
        effective_voice = effective_target.voice if effective_target is not None else None
        project_target = project_targets.get(reference)
        config_target = configured_targets.get(reference)
        if provider is not None and (
            effective_target is None
            or (effective_target.provider or effective_target.engine) != provider
        ):
            continue
        roles.append(
            ProjectRole(
                role=reference,
                uses=value["uses"],
                locations=tuple(value["locations"]),
                document_bindings=dict(value["document_bindings"]),
                project_binding=project_target.voice if project_target else None,
                config_binding=configured_roles.get(reference),
                effective_voice=effective_voice,
                origin=origin or "unresolved",
                status=status,
                effective_by_scope=dict(value["effective_by_scope"]),
                project_target=project_target,
                config_target=config_target,
                effective_target=effective_target,
            )
        )
    providers = {
        role.effective_target.provider
        for role in roles
        if role.effective_target is not None and role.effective_target.provider is not None
    }
    inspection_provider = (
        provider
        if provider is not None
        else (next(iter(providers)) if len(providers) == 1 else None)
    )
    return ProjectRoleInspection(inspection_provider, tuple(roles))


def bind_project_role(
    project: Project,
    cfg: ReadioConfig,
    role: str,
    voice: str,
    *,
    provider: str | None = None,
    engine: str | None = None,
    offline: bool = False,
    refresh: bool = False,
) -> dict[str, Any]:
    """Persist one engine-qualified target for an SSMD role in this project."""
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
    if engine is not None and not engine.strip():
        raise ProjectRoleError(
            "Engine must be a non-empty string.",
            code="readio.project_role.engine_mismatch",
            details={"engine": engine},
        )

    try:
        provider_engine = engine_for_ssmd_provider(provider) if provider is not None else None
        requested_engine = (
            normalize_engine_id(engine)
            if engine is not None
            else provider_engine
            or (normalize_engine_id(provider) if provider in cfg.voices else None)
        )
        if provider_engine is not None and requested_engine != provider_engine:
            raise ProjectRoleError(
                f"Provider {provider!r} maps to engine {provider_engine!r}, "
                f"not requested engine {requested_engine!r}.",
                code="readio.project_role.provider_mismatch",
                details={"provider": provider, "engine": requested_engine},
            )
        selection = resolve_voice_reference(
            requested_voice,
            language=None,
            model=None,
            source=None,
            offline=offline,
            refresh=refresh,
            engine=requested_engine,
        )
    except ModelDiscoveryError as exc:
        if not exc.code.startswith("readio.voice_reference"):
            raise
        raise ProjectRoleError(
            str(exc),
            code="readio.project_role.voice_reference_invalid",
            details={"requested_voice": requested_voice},
        ) from exc
    if selection is None:
        if requested_engine is None:
            raise ProjectRoleError(
                f"Voice {requested_voice!r} requires an engine context.",
                code="readio.project_role.voice_reference_invalid",
                details={"requested_voice": requested_voice},
            )
        try:
            public_system_for_engine(requested_engine)
        except ValueError:
            target = VoiceTarget(requested_engine, requested_voice)
        else:
            raise ProjectRoleError(
                f"Voice {requested_voice!r} is not a valid reference for engine {requested_engine!r}.",
                code="readio.project_role.voice_reference_invalid",
                details={"requested_voice": requested_voice, "engine": requested_engine},
            )
    else:
        target_engine = normalize_engine_id(selection.engine)
        if requested_engine is not None and target_engine != requested_engine:
            raise ProjectRoleError(
                f"Voice reference {selection.ref!r} resolves to engine {target_engine!r}, "
                f"not requested engine {requested_engine!r}.",
                code=(
                    "readio.project_role.provider_mismatch"
                    if provider is not None
                    else "readio.project_role.engine_mismatch"
                ),
                details={"engine": requested_engine, "reference_engine": target_engine},
            )
        target = VoiceTarget(
            target_engine,
            selection.voice,
            target_id=selection.target_id,
        )
    target_provider = target.provider or target.engine
    if provider is not None and target_provider != provider:
        raise ProjectRoleError(
            f"Voice target engine {target.engine!r} uses provider {target_provider!r}, "
            f"not requested provider {provider!r}.",
            code="readio.project_role.provider_mismatch",
            details={"provider": provider, "engine": target.engine},
        )
    selected_provider = target_provider

    inspection = inspect_project_roles(project, cfg)
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
        document_voice = (
            next(iter(set(bindings.values()))) if len(set(bindings.values())) == 1 else "mixed"
        )
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
        lambda manifest: with_project_role_binding(manifest, role=role, target=target),
        operation=f"plan-bind-{role}",
    )
    effective = next(
        item for item in inspect_project_roles(updated, cfg).roles if item.role == role
    )
    return {
        "ok": True,
        "project": str(updated.root),
        "provider": selected_provider,
        "engine": target.engine,
        "target": target.to_dict(),
        "role": role,
        "requested_voice": voice,
        "stored_voice": target.voice,
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
    """Remove one project-local role target and report the newly exposed value."""
    role = role.strip()
    if not role:
        raise ProjectRoleError(
            "Role must be a non-empty string.",
            code="readio.project_role.binding_missing",
            details={"role": role, "provider": provider},
        )

    targets = project_role_bindings(project.manifest)
    target = targets.get(role)
    if target is not None:
        selected_provider = target.provider
        if provider is not None and provider != selected_provider:
            raise ProjectRoleError(
                f"Role {role!r} is bound to provider {selected_provider!r}, not {provider!r}.",
                code="readio.project_role.binding_missing",
                details={"role": role, "provider": provider},
            )
        updated = update_project_manifest(
            project,
            lambda manifest: without_project_role_binding(manifest, role=role),
            operation=f"plan-unbind-{role}",
        )
        removed_voice = target.voice
        removed_target = target
    else:
        selected_provider = resolve_project_voice_provider(
            project.manifest, cfg, explicit_provider=provider
        )
        bindings = project_voice_bindings(project.manifest, selected_provider)
        if role not in bindings:
            raise ProjectRoleError(
                f"No project-local binding exists for role {role!r} and provider "
                f"{selected_provider!r}.",
                code="readio.project_role.binding_missing",
                details={"role": role, "provider": selected_provider},
            )
        removed_voice = bindings[role]
        removed_target = None
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
        "engine": removed_target.engine if removed_target is not None else None,
        "target": removed_target.to_dict() if removed_target is not None else None,
        "removed_target": removed_target,
        "effective_target": effective.effective_target if effective else None,
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
