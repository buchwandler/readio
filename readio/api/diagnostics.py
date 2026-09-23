"""Typed, read-only health and availability reports."""

from __future__ import annotations

import importlib.metadata
import platform
import sys
from typing import TYPE_CHECKING

from .. import __version__
from .. import config as config_internal
from ..errors import ReadioError
from . import errors as api_errors
from .types import (
    AudioFormatDiagnostic,
    DependencyDiagnostic,
    DoctorReport,
    EngineDiagnostic,
    PathDiagnostic,
)

if TYPE_CHECKING:
    from .app import Readio


_DEPENDENCIES = (
    "pykokoro",
    "pipersynth",
    "utterplan",
    "audiocompose",
    "ssmd",
    "sounddevice",
    "soundfile",
)


class DiagnosticsService:
    """Inspect local runtime availability without creating configured paths."""

    def __init__(self, app: Readio) -> None:
        self._app = app

    def run(self) -> DoctorReport:
        try:
            config_path = config_internal.config_path()
            paths = tuple(
                PathDiagnostic(name=name, path=path, exists=path.exists())
                for name, path in (
                    ("templates", self._app.config.paths.templates),
                    ("ingest", self._app.config.paths.ingest),
                    ("output", self._app.config.paths.output),
                )
            )
            return DoctorReport(
                readio_version=__version__,
                python_version=sys.version.split()[0],
                platform=platform.platform(),
                config_path=config_path,
                config_exists=config_path.exists(),
                engines=self.engines(),
                dependencies=self._dependencies(),
                audio_formats=self.audio_formats(),
                paths=paths,
                voice_provider=self._app.config.ssmd.voice_provider,
            )
        except ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.IntegrationError,
                code="diagnostics.run_failed",
            ) from error

    def engines(self) -> tuple[EngineDiagnostic, ...]:
        try:
            result = []
            for engine in self._app.catalog.engines():
                status = (
                    "ready"
                    if engine.runnable
                    else "missing_dependency"
                    if not engine.installed
                    else "unavailable"
                )
                result.append(
                    EngineDiagnostic(
                        id=engine.id,
                        adapter_available=engine.registered,
                        package_available=engine.installed,
                        version=engine.version,
                        status=status,
                        missing_dependency=engine.missing_dependency,
                    )
                )
            return tuple(result)
        except ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.IntegrationError,
                code="diagnostics.engines_failed",
            ) from error

    def audio_formats(self) -> tuple[AudioFormatDiagnostic, ...]:
        try:
            return tuple(
                AudioFormatDiagnostic(
                    id=item.id,
                    suffix=item.suffix,
                    available=item.available,
                    reason=item.reason,
                )
                for item in self._app.catalog.audio_formats()
            )
        except ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.IntegrationError,
                code="diagnostics.audio_formats_failed",
            ) from error

    def _dependencies(self) -> tuple[DependencyDiagnostic, ...]:
        result = []
        for distribution in _DEPENDENCIES:
            try:
                version = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                result.append(DependencyDiagnostic(id=distribution, available=False))
            else:
                result.append(
                    DependencyDiagnostic(id=distribution, available=True, version=version)
                )
        return tuple(result)


__all__ = ["DiagnosticsService"]
