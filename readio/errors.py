"""Errors raised by Readio orchestration boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


class ReadioError(Exception):
    """Base exception for errors produced by Readio orchestration."""

    code = "readio_error"


class CatalogError(ReadioError):
    """A normalized application catalog operation failed."""

    code = "catalog_operation_failed"

    def __init__(
        self,
        message: str,
        *,
        operation: str,
        engine: str | None = None,
        target: str | None = None,
        cause: Exception | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.engine = engine
        self.target = target
        self.cause = cause
        self.details = dict(details or {})


class ModelOperationError(ReadioError):
    """A model lifecycle operation failed at the VoiceRender boundary."""

    code = "model_operation_failed"

    def __init__(
        self,
        message: str,
        *,
        operation: str,
        engine: str,
        target: str,
        quality: str | None = None,
        cause: Exception | None = None,
        details: Mapping[str, object] | None = None,
        recoverable: bool = False,
        suggested_action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.engine = engine
        self.target = target
        self.quality = quality
        self.cause = cause
        self.details = {
            "operation": operation,
            "engine": engine,
            "target": target,
            "quality": quality,
            **dict(details or {}),
        }
        self.recoverable = recoverable
        self.suggested_action = suggested_action


class WorkflowError(ReadioError):
    """Base for errors raised while resolving or executing a Readio workflow."""

    code = "workflow_error"
    recoverable = False
    suggested_action: str | None = None

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        cause: Exception | None = None,
        details: Mapping[str, object] | None = None,
        recoverable: bool | None = None,
        suggested_action: str | None = None,
        stage: str | None = None,
        package: str | None = None,
        selector: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code if code is not None else type(self).code
        self.cause = cause
        self.details = dict(details or {})
        self.recoverable = type(self).recoverable if recoverable is None else recoverable
        self.suggested_action = (
            suggested_action if suggested_action is not None else type(self).suggested_action
        )
        self.stage = stage
        self.package = package
        self.selector = selector


class WorkflowCancelledError(WorkflowError):
    """A synchronous workflow was cancelled at a cooperative stage boundary."""

    code = "cancellation_requested"
    recoverable = True
    suggested_action = "inspect_then_retry"

    def __init__(self, message: str = "workflow cancelled", *, stage: str | None = None) -> None:
        super().__init__(message, stage=stage)


class WorkflowResolutionError(WorkflowError):
    """The workflow request cannot be resolved safely."""

    code = "invalid_request"


class WorkflowStateError(WorkflowError):
    """Inspected specialist state is unsafe or insufficient for convergence."""

    code = "unsafe_workflow_state"


class WorkflowReplanError(WorkflowError):
    """A bounded specialist replan could not make forward progress."""

    code = "workflow_replan_failed"


class OutputCollisionError(WorkflowError):
    """A requested final output already exists and replacement was not permitted."""

    code = "output_exists"
    recoverable = True
    suggested_action = "set_replace_output"


class WorkflowStageError(WorkflowError):
    """A specialist stage failed, with its original cause and workflow context retained."""

    code = "stage_failed"

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
        recoverable: bool = False,
        suggested_action: str | None = None,
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
        super().__init__(
            display,
            code=code,
            cause=cause,
            details=details,
            recoverable=recoverable,
            suggested_action=suggested_action,
            stage=stage,
            package=package,
            selector=selector,
        )
        self.source = source
        self.workspace = workspace
        self.variant = variant


class ReadioStageError(WorkflowStageError):
    """Backward-compatible stage error name used by existing stage adapters."""
