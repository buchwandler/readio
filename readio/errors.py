from __future__ import annotations

from pathlib import Path


class ReadioError(Exception):
    code = "readio.error"

    def __init__(self, message: str, *, source_path: Path | None = None) -> None:
        super().__init__(message)
        self.source_path = source_path


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
        source_path: Path | None = None,
    ) -> None:
        super().__init__(message, source_path=source_path)
        self.provider = provider
        self.reference = reference


class RenderError(ReadioError):
    code = "render.error"
