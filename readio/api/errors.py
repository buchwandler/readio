"""Stable exception types exposed by :mod:`readio.api`."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from ..errors import InputError, ReadioError
from ..jsonutil import JsonValue

if TYPE_CHECKING:
    from .types import Diagnostic, ResolvedPlan


class InvalidRequestError(ReadioError):
    code = "request.invalid"


class DiscoveryError(ReadioError):
    code = "discovery.error"


class ResolutionError(ReadioError):
    code = "resolution.error"


class ExecutionError(ReadioError):
    code = "execution.error"


class OutputError(ExecutionError):
    code = "output.error"


def _diagnostics_details(
    diagnostics: tuple[Diagnostic, ...],
) -> dict[str, JsonValue]:
    return {"diagnostics": [item.to_dict() for item in diagnostics]}


class PlanNotExecutableError(ExecutionError):
    code = "speech.plan_not_executable"

    def __init__(
        self,
        message: str,
        *,
        plan: ResolvedPlan,
        diagnostics: tuple[Diagnostic, ...],
    ) -> None:
        self.plan = plan
        self.diagnostics = diagnostics
        super().__init__(message, details=_diagnostics_details(diagnostics))


class PlannedOutputError(OutputError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        plan: ResolvedPlan,
        diagnostics: tuple[Diagnostic, ...],
    ) -> None:
        self.plan = plan
        self.diagnostics = diagnostics
        super().__init__(
            message,
            code=code,
            details=_diagnostics_details(diagnostics),
        )


class ProjectError(ReadioError):
    code = "project.error"


class ProjectNotFoundError(ProjectError):
    code = "project.not_found"


class ProjectConflictError(ProjectError):
    code = "project.conflict"


class ProjectFormatError(ProjectError):
    code = "project.invalid"


class IntegrationError(ReadioError):
    code = "integration.error"


ErrorT = TypeVar("ErrorT", bound=ReadioError)


def translate_exception(
    error: Exception,
    *,
    error_type: type[ErrorT] | None = None,
    code: str | None = None,
    source_path: Path | None = None,
) -> ReadioError:
    """Translate a provider or built-in exception at a public service boundary."""
    if isinstance(error, ReadioError):
        return error

    if error_type is None:
        if isinstance(error, FileNotFoundError):
            error_type = InputError
            code = code or "input.not_found"
        elif isinstance(error, FileExistsError):
            error_type = OutputError
            code = code or "output.exists"
        elif isinstance(error, (TypeError, ValueError, KeyError)):
            error_type = InvalidRequestError
            code = code or "request.invalid"
        elif isinstance(error, OSError):
            error_type = ExecutionError
        else:
            error_type = ExecutionError

    filename = getattr(error, "filename", None)
    if source_path is None and filename is not None:
        source_path = Path(filename)
    return error_type(
        str(error),
        source_path=source_path,
        details={"exception_type": type(error).__name__},
        code=code,
    )


@contextmanager
def error_boundary(
    *,
    error_type: type[ReadioError] | None = None,
    code: str | None = None,
    source_path: Path | None = None,
) -> Iterator[None]:
    """Translate exceptions raised inside an internal operation.

    Callers should invoke user-provided callbacks outside this boundary so their
    exceptions remain unchanged.
    """
    try:
        yield
    except ReadioError:
        raise
    except Exception as error:
        raise translate_exception(
            error,
            error_type=error_type,
            code=code,
            source_path=source_path,
        ) from error


__all__ = [
    "DiscoveryError",
    "ExecutionError",
    "InputError",
    "IntegrationError",
    "InvalidRequestError",
    "OutputError",
    "PlanNotExecutableError",
    "PlannedOutputError",
    "ProjectConflictError",
    "ProjectError",
    "ProjectFormatError",
    "ProjectNotFoundError",
    "ReadioError",
    "ResolutionError",
    "error_boundary",
    "translate_exception",
]
