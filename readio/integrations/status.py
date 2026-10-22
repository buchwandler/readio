"""Read-only status adapters that use specialist package-root APIs only."""

from __future__ import annotations

import importlib
import importlib.util
from typing import Any

import audiocompose  # type: ignore[import-not-found]
import ssmdconvert  # type: ignore[import-not-found]
import utterplan  # type: ignore[import-not-found]
import voicerender  # type: ignore[import-not-found]

from readio.diagnostics import logger_for, timed_span
from readio.workspace import WorkspaceRef

_LOGGER = logger_for("status")


def audioexport_installed() -> bool:
    """Return whether the optional AudioExport package is importable."""
    return importlib.util.find_spec("audioexport") is not None


AUDIOEXPORT_UNAVAILABLE = object()


def inspect_bundle(workspace: WorkspaceRef) -> Any:
    """Validate and load the editable bundle through SSMDConvert's public API."""
    with timed_span(
        _LOGGER, "status.inspect", package="ssmdconvert", operation="load_bundle_workspace"
    ):
        return ssmdconvert.load_bundle_workspace(workspace.root)


def inspect_compile(workspace: WorkspaceRef, chapters: str) -> Any | None:
    """Inspect compile state if this UtterPlan release exposes a read-only API."""
    operation = "inspect_bundle_compile"
    with timed_span(_LOGGER, "status.inspect", package="utterplan", operation=operation):
        inspector = getattr(utterplan, operation, None)
        if not callable(inspector):
            return None
        return inspector(workspace.root, chapters=chapters)


def inspect_variants(workspace: WorkspaceRef) -> Any:
    """Inspect exact RenderPlan variants and render state via VoiceRender."""
    with timed_span(
        _LOGGER,
        "status.inspect",
        package="voicerender",
        operation="list_bundle_variants",
    ):
        return voicerender.list_bundle_variants(workspace.root)


def inspect_compose(workspace: WorkspaceRef, variant: str, chapters: str) -> Any | None:
    """Inspect composition state if AudioCompose exposes a read-only API."""
    operation = "inspect_bundle_composition"
    with timed_span(_LOGGER, "status.inspect", package="audiocompose", operation=operation):
        inspector = getattr(audiocompose, operation, None)
        if not callable(inspector):
            return None
        return inspector(workspace.root, variant=variant, chapters=chapters)


def inspect_export(workspace: WorkspaceRef, variant: str, chapters: str) -> Any:
    """Inspect optional AudioExport state without importing its private modules."""
    operation = "inspect_bundle_export"
    with timed_span(_LOGGER, "status.inspect", package="audioexport", operation=operation):
        if importlib.util.find_spec("audioexport") is None:
            return AUDIOEXPORT_UNAVAILABLE
        package = importlib.import_module("audioexport")
        inspector = getattr(package, operation, None)
        if not callable(inspector):
            return None
        return inspector(workspace.root, variant=variant, chapters=chapters)


__all__ = [
    "AUDIOEXPORT_UNAVAILABLE",
    "inspect_bundle",
    "inspect_compile",
    "inspect_compose",
    "inspect_export",
    "inspect_variants",
]
