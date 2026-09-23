"""Safe creation and listing for Readio ingest documents."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .. import ingest as ingest_internal
from ..errors import ReadioError
from . import errors as api_errors

if TYPE_CHECKING:
    from .app import Readio


class IngestService:
    """Use the configured ingest directory and existing safe-name rules."""

    def __init__(self, app: Readio) -> None:
        self._app = app

    def directory(self) -> Path:
        return self._app.config.paths.ingest

    def list(self) -> tuple[Path, ...]:
        try:
            return tuple(ingest_internal.list_ingest(self.directory()))
        except ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.InputError,
                code="ingest.list_failed",
            ) from error

    def create(
        self,
        *,
        name: str | None = None,
        template: str | None = None,
    ) -> Path:
        try:
            return ingest_internal.new_ingest(
                self.directory(),
                name=name,
                template_directory=self._app.config.paths.templates,
                template=template,
            )
        except ReadioError:
            raise
        except Exception as error:
            if isinstance(error, OSError):
                error_type = api_errors.OutputError
            else:
                error_type = api_errors.InvalidRequestError
            raise api_errors.translate_exception(
                error,
                error_type=error_type,
                code="ingest.create_failed",
            ) from error


__all__ = ["IngestService"]
