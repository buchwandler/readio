"""Errors raised by Readio orchestration boundaries."""

from __future__ import annotations

from pathlib import Path


class ReadioError(Exception):
    """Base exception for errors produced by Readio orchestration."""


class ReadioStageError(ReadioError):
    """A dependency failure annotated once with stage and workspace context."""

    def __init__(
        self,
        stage: str,
        message: str,
        *,
        workspace: Path,
        code: str | None = None,
        cause: Exception,
    ) -> None:
        super().__init__(f"{stage} failed for {workspace}: {message}")
        self.stage = stage
        self.workspace = workspace
        self.code = code
        self.cause = cause
