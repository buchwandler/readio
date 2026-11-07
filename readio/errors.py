"""Errors raised by Readio orchestration boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


class ReadioError(Exception):
    """Base exception for errors produced by Readio orchestration."""


class WorkflowError(ReadioError):
    """Base for errors raised while resolving or executing a Readio workflow."""

    def __init__(
        self,
        message: str,
        *,
        cause: Exception | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.cause = cause
        self.details = dict(details or {})


class WorkflowResolutionError(WorkflowError):
    """The workflow request cannot be resolved safely."""


class WorkflowStateError(WorkflowError):
    """Inspected specialist state is unsafe or insufficient for convergence."""


class WorkflowReplanError(WorkflowError):
    """A bounded specialist replan could not make forward progress."""


class OutputCollisionError(WorkflowError):
    """A requested final output already exists and replacement was not permitted."""


class WorkflowStageError(WorkflowError):
    """A specialist stage failed, with its original cause and workflow context retained."""

    def __init__(
        self,
        stage: str,
        message: str,
        *,
        package: str | None = None,
        source: Path | None = None,
        workspace: Path | None = None,
        variant: str | None = None,
        selector: str | None = None,
        code: str | None = None,
        cause: Exception,
        details: Mapping[str, object] | None = None,
    ) -> None:
        if package is None and source is None and workspace is not None:
            display = f"{stage} failed for {workspace}: {message}"
        else:
            context = f"{package} {stage}" if package is not None else stage
            if selector is not None:
                context = f"{context} for {selector}"
            if variant is not None:
                context = f"{context} ({variant})"
            if source is not None:
                context = f"{context} from {source}"
            display = f"Readio workflow failed during {context}: {message}"
        super().__init__(display, cause=cause, details=details)
        self.stage = stage
        self.package = package
        self.source = source
        self.workspace = workspace
        self.variant = variant
        self.selector = selector
        self.code = code
        self.cause = cause


class ReadioStageError(WorkflowStageError):
    """Backward-compatible stage error name used by existing stage adapters."""
