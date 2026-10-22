"""Optional AudioExport public API adapter for SSMD bundle workspaces."""

from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

from readio.errors import ReadioError
from readio.workspace import WorkspaceRef

if TYPE_CHECKING:
    from readio.api.types import ExportRequest


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


def supported_formats() -> Mapping[str, Any]:
    """Return AudioExport's authoritative output format specifications."""
    formats = getattr(_audioexport_api(), "FORMATS", None)
    if not isinstance(formats, Mapping) or not formats:
        raise ReadioError("AudioExport does not expose its supported format registry.")
    return formats


def preflight_export(
    format_name: str,
    *,
    ffmpeg: str | Path | None = None,
    ffprobe: str | Path | None = None,
) -> Mapping[str, Any]:
    """Check AudioExport, FFmpeg, FFprobe, and the selected encoder via its doctor API."""
    api = _audioexport_api()
    formats = supported_formats()
    if format_name not in formats:
        raise ReadioError(f"AudioExport does not support output format {format_name!r}.")
    doctor = getattr(api, "doctor", None)
    if not callable(doctor):
        raise ReadioError(
            "This AudioExport version does not expose doctor preflight; upgrade AudioExport."
        )
    try:
        report = doctor(ffmpeg=ffmpeg, ffprobe=ffprobe, requested_formats=[format_name])
    except Exception as error:
        raise ReadioError(f"AudioExport preflight failed for {format_name!r}: {error}") from error
    if not isinstance(report, Mapping):
        raise ReadioError("AudioExport doctor returned an invalid preflight report.")
    if report.get("requested_formats_ready") is not True:
        problems: list[str] = []
        tools = report.get("tools", {})
        if isinstance(tools, Mapping):
            for name in ("ffmpeg", "ffprobe"):
                tool = tools.get(name)
                if isinstance(tool, Mapping) and tool.get("available") is not True:
                    problems.append(str(tool.get("error") or f"{name} is unavailable"))
        format_reports = report.get("formats", {})
        if isinstance(format_reports, Mapping):
            selected = format_reports.get(format_name)
            if isinstance(selected, Mapping) and selected.get("available") is not True:
                problems.append(
                    str(selected.get("reason") or f"{format_name} encoder is unavailable")
                )
        explanation = (
            "; ".join(dict.fromkeys(problems)) or "required export tools or encoder are unavailable"
        )
        raise ReadioError(
            f"AudioExport preflight failed for {format_name!r}: {explanation}. "
            "Install the required FFmpeg tools/encoder or pass --ffmpeg and --ffprobe overrides."
        )
    return report
