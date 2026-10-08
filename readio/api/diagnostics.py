"""Typed, read-only health and availability reports."""

from __future__ import annotations

import importlib.metadata
import platform
import sys
from typing import TYPE_CHECKING

from .. import __version__
from .. import config as config_internal
from ..engines.registry import engine_status
from ..errors import ReadioError
from . import errors as api_errors
from .types import (
    AudioFormatDiagnostic,
    DependencyDiagnostic,
    DoctorReport,
    EngineDiagnostic,
    PathDiagnostic,
)

_ENGINE_DISTRIBUTIONS = {
    "kokoro": "pykokoro",
    "piper": "pipersynth",
    "pocket": "pocketsynth",
    "kitten": "kittensynth",
    "supertonic": "supertonicsynth",
    "inflect": "inflectsynth",
}

if TYPE_CHECKING:
    from .app import Readio


_DEPENDENCIES = (
    "pykokoro",
    "pipersynth",
    "pocketsynth",
    "utterplan",
    "audiocompose",
    "ssmd",
    "ssmdconvert",
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
                engine=self._app.config.reader.engine,
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
            for engine_id, status in engine_status().items():
                package_available = bool(status["package"])
                result.append(
                    EngineDiagnostic(
                        id=engine_id,
                        adapter_available=bool(status["adapter"]),
                        package_available=package_available,
                        version=status["version"],
                        status=status["status"],
                        missing_dependency=(
                            None
                            if package_available
                            else _ENGINE_DISTRIBUTIONS.get(engine_id, engine_id)
                        ),
                        module_version=status["module_version"],
                        module_path=status["module_path"],
                        request_api_version=status["request_api_version"],
                        expected_request_api_version=status["expected_request_api_version"],
                        contract_source=status["contract_source"],
                        api_compatible=status["api_compatible"],
                        missing_symbols=tuple(status["missing_symbols"]),
                        missing_methods=tuple(status["missing_methods"]),
                        failed_stage=status["failed_stage"],
                        failed_symbol=status["failed_symbol"],
                        error_type=status["error_type"],
                        error_message=status["error_message"],
                        warnings=tuple(status["warnings"]),
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
