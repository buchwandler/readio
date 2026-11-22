"""Stable application-facing error descriptions."""

from __future__ import annotations

from readio.api.types import ErrorInfo
from readio.errors import ReadioError


def describe_error(error: ReadioError) -> ErrorInfo:
    """Project an expected Readio error into its stable application DTO."""
    if not isinstance(error, ReadioError):
        raise TypeError("describe_error expects a ReadioError")
    return ErrorInfo(
        type=type(error).__name__,
        message=str(error),
        code=getattr(error, "code", None),
        stage=getattr(error, "stage", None),
        package=getattr(error, "package", None),
        selector=getattr(error, "selector", None),
        details=dict(getattr(error, "details", {})),
        recoverable=getattr(error, "recoverable", False),
        suggested_action=getattr(error, "suggested_action", None),
    )


__all__ = ["describe_error"]
