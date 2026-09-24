"""Global and project-local logical voice role operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from .. import config as config_internal
from .. import project as project_internal
from ..engines.registry import ssmd_provider_for_engine
from ..jsonutil import JsonValue, json_value
from ..project_roles import ProjectRoleError as InternalProjectRoleError
from ..project_roles import (
    bind_project_role,
    inspect_project_roles,
    unbind_project_role,
)
from ..voices import resolve_voice_selector
from . import errors as api_errors
from .types import (
    DiscoveryOptions,
    ProjectLike,
    ProjectRole,
    ProjectRoleInspection,
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
        provider_id = provider or self._app.config.ssmd.voice_provider
        settings = self._app.config.voices.get(provider_id)
        if settings is None:
            raise api_errors.InvalidRequestError(
                f"voice provider {provider_id!r} is not configured",
                code="roles.provider_not_configured",
            )
        return tuple(
            RoleBinding(provider=provider_id, role=role, voice=voice)
            for role, voice in sorted(settings.roles.items())
        )

    def bind_global(
        self,
        role: str,
        voice: str,
        *,
        provider: str | None = None,
        discovery: DiscoveryOptions = _DEFAULT_DISCOVERY,
    ) -> RoleBinding:
        provider_id = provider or self._app.config.ssmd.voice_provider
        try:
            resolved = resolve_voice_selector(
                voice,
                language=None,
                model=None,
                source=None,
                offline=discovery.offline,
                refresh=discovery.refresh,
                preference=discovery.preference,
            )
            stored_voice = resolved.voice if resolved is not None and resolved.selector else voice
            selected_engine = (
                resolved.engine if resolved is not None and resolved.selector else None
            )
            if selected_engine is not None:
                selected_provider = ssmd_provider_for_engine(selected_engine)
                if selected_provider is not None and selected_provider != provider_id:
                    raise ValueError(
                        f"voice selector {voice!r} belongs to provider {selected_provider!r}, "
                        f"not {provider_id!r}"
                    )
            updated = config_internal.bind_voice_role(
                self._app.config,
                role,
                stored_voice,
                provider_id,
            )
            config_internal.save_config(updated)
        except api_errors.ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.ResolutionError,
                code="roles.bind_global_failed",
            ) from error
        return RoleBinding(provider=provider_id, role=role, voice=stored_voice)

    def unbind_global(self, role: str, *, provider: str | None = None) -> None:
        provider_id = provider or self._app.config.ssmd.voice_provider
        try:
            updated = config_internal.unbind_voice_role(self._app.config, role, provider_id)
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
        internal = self._load_project(project)
        try:
            unbind_project_role(internal, self._app.config, role, provider=provider)
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
        )


__all__ = ["RoleService"]
