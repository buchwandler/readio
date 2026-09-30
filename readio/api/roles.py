"""Global and project-local logical voice role operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from .. import config as config_internal
from .. import project as project_internal
from ..engines.registry import engine_for_ssmd_provider, normalize_engine_id
from ..jsonutil import JsonValue, json_value
from ..project_roles import ProjectRoleError as InternalProjectRoleError
from ..project_roles import (
    bind_project_role,
    inspect_project_roles,
    unbind_project_role,
)
from ..role_targets import VoiceTarget
from ..voices import resolve_voice_selector
from . import errors as api_errors
from .types import (
    DiscoveryOptions,
    ProjectLike,
    ProjectRef,
    ProjectRole,
    ProjectRoleInspection,
    ProjectRoleMutationResult,
    RoleBinding,
    RoleLocation,
)

if TYPE_CHECKING:
    from .app import Readio


_DEFAULT_DISCOVERY = DiscoveryOptions()


class RoleService:
    """Resolve and persist logical voice roles without merging binding layers."""

    def __init__(self, app: Readio) -> None:
        self._app = app

    def list_global(self, *, provider: str | None = None) -> tuple[RoleBinding, ...]:
        cfg = self._app.config
        if (
            provider is not None
            and provider not in cfg.voices
            and not any(target.provider == provider for target in cfg.roles.values())
        ):
            raise api_errors.InvalidRequestError(
                f"voice provider {provider!r} is not configured",
                code="roles.provider_not_configured",
            )
        try:
            targets = config_internal.role_targets(cfg, provider=provider)
        except ValueError as error:
            raise api_errors.ResolutionError(
                str(error), code="roles.ambiguous_legacy_binding"
            ) from error
        return tuple(RoleBinding(role=role, target=target) for role, target in targets.items())

    def bind_global(
        self,
        role: str,
        voice: str,
        *,
        provider: str | None = None,
        engine: str | None = None,
        discovery: DiscoveryOptions = _DEFAULT_DISCOVERY,
    ) -> RoleBinding:
        cfg = self._app.config
        try:
            provider_engine = engine_for_ssmd_provider(provider) if provider is not None else None
            requested_engine = (
                normalize_engine_id(engine) if engine is not None else provider_engine
            )
            if provider_engine is not None and requested_engine != provider_engine:
                raise ValueError(
                    f"provider {provider!r} maps to engine {provider_engine!r}, "
                    f"not requested engine {requested_engine!r}"
                )
            resolved = resolve_voice_selector(
                voice,
                language=None,
                model=None,
                source=None,
                offline=discovery.offline,
                refresh=discovery.refresh,
                preference=discovery.preference,
                engine=requested_engine,
            )
            if resolved is not None and resolved.selector is not None:
                target_engine = normalize_engine_id(resolved.engine or "")
                if requested_engine is not None and target_engine != requested_engine:
                    raise ValueError(
                        f"voice selector {voice!r} resolves to engine {target_engine!r}, "
                        f"not requested engine {requested_engine!r}"
                    )
                target = VoiceTarget(
                    target_engine,
                    resolved.voice,
                    target_id=resolved.model,
                    selector=resolved.selector,
                )
            else:
                if requested_engine is None:
                    matches = [
                        name for name, settings in cfg.voices.items() if voice in settings.ids
                    ]
                    if len(matches) == 1:
                        requested_engine = engine_for_ssmd_provider(matches[0])
                    elif len(matches) > 1:
                        raise ValueError(
                            f"raw voice ID {voice!r} is configured for multiple engines; "
                            "specify engine explicitly"
                        )
                    elif len(cfg.voices) == 1:
                        requested_engine = engine_for_ssmd_provider(next(iter(cfg.voices)))
                    else:
                        raise ValueError(
                            f"raw voice ID {voice!r} does not identify an engine; "
                            "specify engine explicitly"
                        )
                target = VoiceTarget(
                    requested_engine,
                    resolved.voice if resolved is not None else voice,
                    target_id=resolved.model if resolved is not None else None,
                )
            if provider is not None and target.provider != provider:
                raise ValueError(
                    f"voice target engine {target.engine!r} uses provider {target.provider!r}, "
                    f"not requested provider {provider!r}"
                )
            updated = config_internal.bind_voice_target(cfg, role, target)
            config_internal.save_config(updated)
        except api_errors.ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.ResolutionError,
                code="roles.bind_global_failed",
            ) from error
        return RoleBinding(role=role, target=target)

    def unbind_global(self, role: str, *, provider: str | None = None) -> None:
        cfg = self._app.config
        try:
            if role in cfg.roles:
                target = cfg.roles[role]
                if provider is not None and target.provider != provider:
                    raise ValueError(
                        f"voice role {role!r} is bound to provider {target.provider!r}, "
                        f"not {provider!r}"
                    )
                updated = config_internal.unbind_voice_target(cfg, role)
            else:
                targets = config_internal.role_targets(cfg, provider=provider)
                target = targets.get(role)
                if target is None:
                    raise ValueError(f"voice role {role!r} is not configured")
                legacy_provider = provider or target.provider
                if legacy_provider is None:
                    raise ValueError(f"voice role {role!r} has no provider mapping")
                updated = config_internal.unbind_voice_role(cfg, role, legacy_provider)
            config_internal.save_config(updated)
        except api_errors.ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.InvalidRequestError,
                code="roles.unbind_global_failed",
            ) from error

    def inspect_project(
        self,
        project: ProjectLike,
        *,
        provider: str | None = None,
    ) -> ProjectRoleInspection:
        internal = self._load_project(project)
        try:
            inspection = inspect_project_roles(internal, self._app.config, provider=provider)
            return ProjectRoleInspection(
                provider=inspection.provider,
                roles=tuple(self._project_role(role) for role in inspection.roles),
            )
        except api_errors.ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.ResolutionError,
                code="roles.inspect_project_failed",
            ) from error

    def bind_project(
        self,
        project: ProjectLike,
        role: str,
        voice: str,
        *,
        provider: str | None = None,
        engine: str | None = None,
        discovery: DiscoveryOptions = _DEFAULT_DISCOVERY,
    ) -> ProjectRole:
        internal = self._load_project(project)
        try:
            result = bind_project_role(
                internal,
                self._app.config,
                role,
                voice,
                provider=provider,
                engine=engine,
                offline=discovery.offline,
                refresh=discovery.refresh,
            )
            inspection = inspect_project_roles(
                project_internal.load_project(result["project"]),
                self._app.config,
                provider=str(result["provider"]),
            )
            selected = next(item for item in inspection.roles if item.role == role)
            return self._project_role(selected)
        except InternalProjectRoleError as error:
            raise api_errors.ResolutionError(
                str(error),
                details=cast(dict[str, JsonValue], json_value(error.details)),
                code=error.code,
            ) from error
        except api_errors.ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.ResolutionError,
                code="roles.bind_project_failed",
            ) from error

    def unbind_project(
        self,
        project: ProjectLike,
        role: str,
        *,
        provider: str | None = None,
    ) -> None:
        self.unbind_project_result(project, role, provider=provider)

    def unbind_project_result(
        self,
        project: ProjectLike,
        role: str,
        *,
        provider: str | None = None,
    ) -> ProjectRoleMutationResult:
        internal = self._load_project(project)
        try:
            result = unbind_project_role(internal, self._app.config, role, provider=provider)
            effective_voice = result["effective_voice"]
            origin = result["origin"]
            status = (
                "mixed"
                if origin == "mixed"
                else "unresolved"
                if effective_voice is None
                else "resolved"
            )
            return ProjectRoleMutationResult(
                project=self._project_ref(internal),
                role=str(result["role"]),
                previous_project_binding=(
                    result["removed_voice"] if isinstance(result["removed_voice"], str) else None
                ),
                project_binding=None,
                effective_voice=(effective_voice if isinstance(effective_voice, str) else None),
                origin=origin if isinstance(origin, str) else None,
                previous_project_target=(
                    result["removed_target"]
                    if isinstance(result["removed_target"], VoiceTarget)
                    else None
                ),
                project_target=None,
                effective_target=(
                    result["effective_target"]
                    if isinstance(result["effective_target"], VoiceTarget)
                    else None
                ),
                status=status,
            )
        except InternalProjectRoleError as error:
            raise api_errors.ResolutionError(
                str(error),
                details=cast(dict[str, JsonValue], json_value(error.details)),
                code=error.code,
            ) from error
        except api_errors.ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.ResolutionError,
                code="roles.unbind_project_failed",
            ) from error

    def _load_project(self, project: ProjectLike) -> project_internal.Project:
        path = project.root if hasattr(project, "root") else project
        try:
            return project_internal.load_project(path)
        except project_internal.ProjectError as error:
            raise api_errors.ProjectNotFoundError(str(error), code="project.not_found") from error

    def _project_ref(self, project: project_internal.Project) -> ProjectRef:
        manifest = project.manifest
        return ProjectRef(
            root=project.root,
            project_id=manifest.project_id,
            name=manifest.name,
            kind=manifest.kind,
            source_format=manifest.source_format,
        )

    def _project_role(self, role) -> ProjectRole:
        return ProjectRole(
            role=role.role,
            uses=role.uses,
            locations=tuple(
                RoleLocation(scope_id=item.scope_id, lines=tuple(item.lines))
                for item in role.locations
            ),
            document_bindings=dict(role.document_bindings),
            project_binding=role.project_binding,
            config_binding=role.config_binding,
            effective_voice=role.effective_voice,
            origin=role.origin,
            status=role.status,
            effective_by_scope=cast(
                dict[str, dict[str, JsonValue]], json_value(role.effective_by_scope)
            ),
            project_target=role.project_target,
            config_target=role.config_target,
            effective_target=role.effective_target,
        )


__all__ = ["RoleService"]
