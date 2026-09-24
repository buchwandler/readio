from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .jsonutil import JsonValue


class ReadioError(Exception):
    code = "readio.error"

    def __init__(
        self,
        message: str,
        *,
        source_path: Path | None = None,
        details: Mapping[str, JsonValue] | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code or type(self).code
        self.source_path = source_path
        self.details = dict(details or {})


class InputError(ReadioError):
    code = "input.error"


class SSMDInputError(ReadioError):
    code = "ssmd.input_invalid"


class VoiceResolutionError(SSMDInputError):
    code = "ssmd.unresolved_voice_role"

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        reference: str,
        references: tuple[Any, ...] = (),
        available_voices: tuple[str, ...] = (),
        header_template: dict[str, JsonValue] | None = None,
        source_path: Path | None = None,
    ) -> None:
        reference_details: list[JsonValue] = [
            {
                "name": item.reference,
                "count": item.count,
                "lines": list(item.lines),
            }
            for item in references
        ]
        details: dict[str, JsonValue] = {
            "provider": provider,
            "reference": reference,
            "references": reference_details,
            "available_voices": list(available_voices),
            "header_template": header_template or {},
        }
        super().__init__(message, source_path=source_path, details=details)
        self.provider = provider
        self.reference = reference
        self.references = references
        self.available_voices = available_voices
        self.header_template = header_template or {}


class RenderError(ReadioError):
    code = "render.error"


class ManifestError(RenderError):
    code = "render.manifest_error"

    def __init__(
        self,
        message: str,
        *,
        audio_path: Path,
        manifest_path: Path,
    ) -> None:
        super().__init__(message)
        self.audio_path = audio_path
        self.manifest_path = manifest_path
