"""User-template listing and safe management operations."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .. import templates as templates_internal
from ..errors import ReadioError
from . import errors as api_errors
from .types import Diagnostic, TemplateInfo, TemplateValidationResult

if TYPE_CHECKING:
    from .app import Readio


class TemplateService:
    """Manage user templates while retaining safe-child and atomic-write rules."""

    def __init__(self, app: Readio) -> None:
        self._app = app

    def directory(self) -> Path:
        return self._app.config.paths.templates

    def list(self) -> tuple[TemplateInfo, ...]:
        try:
            names = templates_internal.list_templates(self.directory())
            return tuple(
                TemplateInfo(
                    name=name, path=templates_internal.template_path(self.directory(), name)
                )
                for name in names
            )
        except ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.InputError,
                code="template.list_failed",
            ) from error

    def show(self, name: str) -> str:
        path = self.path(name)
        try:
            return templates_internal.show_template(self.directory(), path.name)
        except ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.InputError,
                code="template.read_failed",
                source_path=path,
            ) from error

    def path(self, name: str) -> Path:
        try:
            return templates_internal.template_path(self.directory(), name)
        except ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.InvalidRequestError,
                code="template.path_invalid",
            ) from error

    def seed(self) -> tuple[Path, ...]:
        try:
            templates_internal.seed_templates(self.directory(), overwrite=False)
            return tuple(
                templates_internal.template_path(self.directory(), name)
                for name in templates_internal.packaged_template_names()
            )
        except ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.OutputError,
                code="template.seed_failed",
            ) from error

    def add(
        self,
        name: str,
        *,
        source: Path | None = None,
        content: str | None = None,
        force: bool = False,
    ) -> Path:
        try:
            return templates_internal.add_template(
                self.directory(),
                name,
                source,
                content=content,
                force=force,
            )
        except ReadioError:
            raise
        except Exception as error:
            if isinstance(error, FileNotFoundError):
                error_type = api_errors.InputError
            elif isinstance(error, OSError):
                error_type = api_errors.OutputError
            else:
                error_type = api_errors.InvalidRequestError
            raise api_errors.translate_exception(
                error,
                error_type=error_type,
                code="template.add_failed",
                source_path=source,
            ) from error

    def remove(self, name: str) -> None:
        try:
            templates_internal.remove_template(self.directory(), name)
        except ReadioError:
            raise
        except Exception as error:
            error_type = (
                api_errors.OutputError
                if isinstance(error, OSError)
                else api_errors.InvalidRequestError
            )
            raise api_errors.translate_exception(
                error,
                error_type=error_type,
                code="template.remove_failed",
            ) from error

    def reset(
        self,
        name: str | None = None,
        *,
        all: bool = False,
    ) -> tuple[Path, ...]:
        if all and name is not None:
            raise api_errors.InvalidRequestError(
                "reset accepts either a template name or all=True",
                code="template.reset_arguments_conflict",
            )
        if all:
            names = templates_internal.packaged_template_names()
        elif name is not None:
            names = (name,)
        else:
            raise api_errors.InvalidRequestError(
                "template reset requires a name or all=True",
                code="template.reset_name_required",
            )
        try:
            return tuple(
                templates_internal.reset_template(self.directory(), item) for item in names
            )
        except ReadioError:
            raise
        except Exception as error:
            error_type = (
                api_errors.OutputError
                if isinstance(error, OSError)
                else api_errors.InvalidRequestError
            )
            raise api_errors.translate_exception(
                error,
                error_type=error_type,
                code="template.reset_failed",
            ) from error

    def validate(self, name: str, *, roundtrip: bool = False) -> TemplateValidationResult:
        path = self.path(name)
        try:
            analysis = self._app.ssmd.analyze(path)
            roundtrip_result = self._app.ssmd.roundtrip_check(path).roundtrip if roundtrip else None
            ok = analysis.ok and (
                roundtrip_result is None or roundtrip_result.get("ok") is not False
            )
            return TemplateValidationResult(
                name=path.stem,
                source_path=path,
                ok=ok,
                analysis=analysis,
                roundtrip=roundtrip_result,
                consumer=analysis,
            )
        except ReadioError as error:
            diagnostic = Diagnostic(
                code=error.code or "template.invalid",
                severity="error",
                message=str(error),
                source_path=path,
                details={"error_type": type(error).__name__},
            )
            return TemplateValidationResult(
                name=path.stem,
                source_path=path,
                ok=False,
                error=diagnostic,
            )
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.InputError,
                code="template.validation_failed",
                source_path=path,
            ) from error


__all__ = ["TemplateService"]
