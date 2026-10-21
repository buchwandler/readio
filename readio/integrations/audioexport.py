"""Optional AudioExport public API adapter for SSMD bundle workspaces."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from readio.api.types import ExportRequest
from readio.errors import ReadioError
from readio.workspace import WorkspaceRef


def export_bundle(workspace: WorkspaceRef, request: ExportRequest) -> Any:
    api = _audioexport_api()
    return api.export_bundle(
        workspace.root,
        variant=request.variant,
        chapters=request.chapters,
        output=request.output,
        format=request.format,
        profile=request.profile,
        bitrate=request.bitrate,
        sample_rate=request.sample_rate,
        channels=request.channels,
        metadata=request.metadata,
        cover=request.cover,
        timeline=request.timeline,
        audiocompose_root=request.audiocompose_root,
        output_root=request.output_root,
        force=request.force,
        ffmpeg=request.ffmpeg,
        ffprobe=request.ffprobe,
    )


def _audioexport_api() -> Any:
    try:
        return import_module("audioexport")
    except ModuleNotFoundError as exc:
        if exc.name != "audioexport":
            raise
        raise ReadioError(
            "AudioExport support is optional; install Readio with the 'audioexport' extra."
        ) from exc
