"""Lazy, Readio-owned boundary for the optional AudioExport backend."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any, NoReturn

from ..errors import ReadioError


class AudioExportUnavailableError(ReadioError):
    """Raised when a selected profile has no AudioExport installation."""

    code = "audioexport.dependency_missing"


class AudioExportIntegrationError(ReadioError):
    """Readio error retaining a typed AudioExport error code and details."""

    code = "audioexport.integration_error"


@dataclass(frozen=True, slots=True)
class ResolvedAudioExport:
    profile_path: Path
    profile: Any
    output_spec: Any
    resolved_output: Any
    audioexport: ModuleType
    bitrate: str | None
    metadata: Mapping[str, str]

    @property
    def format(self) -> str:
        return self.resolved_output.format

    @property
    def filename(self) -> str:
        return self.resolved_output.filename

    @property
    def cover(self) -> Path | None:
        return self.resolved_output.cover

    @property
    def timeline(self) -> Path | None:
        return self.resolved_output.timeline

    @property
    def version(self) -> str:
        return str(self.audioexport.__version__)


def load_audioexport() -> ModuleType:
    """Import AudioExport only when a profile-backed operation is requested."""
    try:
        return import_module("audioexport")
    except ModuleNotFoundError as error:
        if error.name != "audioexport":
            raise
        raise AudioExportUnavailableError(
            "AudioExport profiles require the optional dependency; install readio[audioexport]."
        ) from error


def _raise_audioexport_error(error: Exception) -> NoReturn:
    code = getattr(error, "code", None)
    if isinstance(code, str) and code.startswith("audioexport."):
        details = getattr(error, "details", {})
        if not isinstance(details, Mapping):
            details = {}
        raise AudioExportIntegrationError(str(error), code=code, details=details) from error
    raise error


def resolve_profile_output_row(
    profile_path: Path,
    profile: Any,
    output_spec: Any,
    *,
    master: Path,
    source_stem: str,
    bitrate_override: str | None,
    allowed_formats: Collection[str] | None = None,
) -> ResolvedAudioExport:
    """Resolve and preflight one already-loaded public profile output row."""
    audioexport = load_audioexport()
    absolute_profile = profile_path.expanduser().resolve()
    effective_spec = (
        replace(output_spec, bitrate=bitrate_override)
        if bitrate_override is not None
        else output_spec
    )
    try:
        resolved = audioexport.resolve_output(profile, effective_spec, source_stem)
        if allowed_formats is not None and resolved.format not in allowed_formats:
            raise AudioExportIntegrationError(
                f"{resolved.format.upper()} is not supported by generic project export",
                code="readio.export.format_unsupported",
            )
        if resolved.format != "wav":
            selected_profile = replace(profile, outputs=(effective_spec,))
            checked = audioexport.preflight_profile(selected_profile, master)
            if len(checked) != 1:
                raise AudioExportIntegrationError(
                    "AudioExport preflight returned an unexpected output count",
                    code="readio.export.profile_preflight_invalid",
                )
            resolved = checked[0]
        if resolved.format == "wav" and profile.metadata:
            raise AudioExportIntegrationError(
                "WAV profile exports use Readio's legacy backend, which cannot apply profile metadata",
                code="readio.export.profile_wav_metadata_unsupported",
            )
        selected_bitrate = bitrate_override if bitrate_override is not None else resolved.bitrate
        normalized_bitrate = audioexport.normalize_bitrate(selected_bitrate, resolved.format)
        return ResolvedAudioExport(
            profile_path=absolute_profile,
            profile=profile,
            output_spec=effective_spec,
            resolved_output=resolved,
            audioexport=audioexport,
            bitrate=normalized_bitrate,
            metadata=dict(profile.metadata),
        )
    except AudioExportIntegrationError:
        raise
    except audioexport.AudioExportError as error:
        _raise_audioexport_error(error)


def resolve_profile_export(
    profile_path: Path,
    *,
    master: Path,
    source_stem: str,
    requested_format: str,
    format_explicit: bool,
    bitrate_override: str | None,
    allowed_formats: Collection[str] | None = None,
    preflight: bool = True,
) -> ResolvedAudioExport:
    """Load a profile once, select one output, and preflight it without writes."""
    audioexport = load_audioexport()
    absolute_profile = profile_path.expanduser().resolve()
    try:
        profile = audioexport.load_profile(absolute_profile)
        outputs = tuple(profile.outputs)
        if format_explicit:
            matches = tuple(spec for spec in outputs if spec.format == requested_format)
            if not matches:
                raise AudioExportIntegrationError(
                    f"profile has no {requested_format!r} output; select a configured format",
                    code="readio.export.profile_format_unmatched",
                )
            if len(matches) > 1:
                raise AudioExportIntegrationError(
                    f"profile has multiple {requested_format!r} outputs; Readio exports one output at a time",
                    code="readio.export.profile_format_ambiguous",
                )
            selected = matches[0]
        else:
            if len(outputs) != 1:
                raise AudioExportIntegrationError(
                    "profile has multiple outputs; specify --format to select one output",
                    code="readio.export.profile_format_required",
                )
            selected = outputs[0]

        effective_spec = (
            replace(selected, bitrate=bitrate_override)
            if bitrate_override is not None
            else selected
        )
        resolved = audioexport.resolve_output(profile, effective_spec, source_stem)
        if allowed_formats is not None and resolved.format not in allowed_formats:
            raise AudioExportIntegrationError(
                f"{resolved.format.upper()} is not supported by generic project export",
                code="readio.export.format_unsupported",
            )
        if preflight and resolved.format != "wav":
            selected_profile = replace(profile, outputs=(effective_spec,))
            checked = audioexport.preflight_profile(selected_profile, master)
            if len(checked) != 1:
                raise AudioExportIntegrationError(
                    "AudioExport preflight returned an unexpected output count",
                    code="readio.export.profile_preflight_invalid",
                )
            resolved = checked[0]
        if resolved.format == "wav" and profile.metadata:
            raise AudioExportIntegrationError(
                "WAV profile exports use Readio's legacy backend, which cannot apply profile metadata",
                code="readio.export.profile_wav_metadata_unsupported",
            )
        selected_bitrate = bitrate_override if bitrate_override is not None else resolved.bitrate
        bitrate = audioexport.normalize_bitrate(selected_bitrate, resolved.format)
        return ResolvedAudioExport(
            profile_path=absolute_profile,
            profile=profile,
            output_spec=effective_spec,
            resolved_output=resolved,
            audioexport=audioexport,
            bitrate=bitrate,
            metadata=dict(profile.metadata),
        )
    except AudioExportIntegrationError:
        raise
    except audioexport.AudioExportError as error:
        _raise_audioexport_error(error)


def encode_resolved(
    master: Path,
    target: Path,
    resolved: ResolvedAudioExport,
    *,
    force_authorized: bool,
) -> Any:
    """Encode one preflighted target using Readio-authorized overwrite permission."""
    try:
        return resolved.audioexport.encode(
            master,
            target,
            format=resolved.format,
            bitrate=resolved.bitrate,
            metadata=resolved.metadata,
            cover=resolved.cover,
            timeline=resolved.timeline,
            force=force_authorized,
        )
    except resolved.audioexport.AudioExportError as error:
        _raise_audioexport_error(error)


__all__ = [
    "AudioExportIntegrationError",
    "AudioExportUnavailableError",
    "ResolvedAudioExport",
    "encode_resolved",
    "load_audioexport",
    "resolve_profile_export",
    "resolve_profile_output_row",
]
