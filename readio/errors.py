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
        self.message = message
        self.code = code or type(self).code
        self.source_path = source_path
        self.details = dict(details or {})


class InputError(ReadioError):
    code = "input.error"


class InvalidRendererSegmentError(ReadioError):
    """A semantic renderer segment cannot produce meaningful speech."""

    code = "synthesis.invalid_renderer_segment"

    def __init__(
        self,
        *,
        scope_id: str,
        unit_id: str,
        segment_id: str,
        segment_index: int,
        text: str,
        token_pos: list[str],
        plan_id: str | None = None,
        scope_title: str | None = None,
        scope_number: int | None = None,
    ) -> None:
        context: dict[str, JsonValue] = {
            "scope_id": scope_id,
            "unit_id": unit_id,
            "segment_id": segment_id,
            "segment_index": segment_index,
            "text": text,
            "token_pos": token_pos,
            "reason": "punctuation_only_renderer_segment",
        }
        for key, value in (
            ("plan_id", plan_id),
            ("scope_title", scope_title),
            ("scope_number", scope_number),
        ):
            if value is not None:
                context[key] = value
        message = (
            f"readio: invalid renderer segment in {scope_id}, unit {unit_id}, {segment_id}: "
            f"speech text is punctuation-only {text!r}. Re-plan the project with a corrected "
            "UtterPlan version before synthesis."
        )
        super().__init__(message, details=context)


class EngineSynthesisError(ReadioError):
    code = "synthesis.engine_error"

    def __init__(
        self,
        message: str,
        *,
        engine: str | None = None,
        engine_version: str | None = None,
        target_id: str | None = None,
        language: str | None = None,
        voice: str | None = None,
        speaker: str | int | None = None,
        request_id: str | None = None,
        native_error_type: str | None = None,
        amount: int | None = None,
        maximum: int | None = None,
        unit: str | None = None,
        text_length: int | None = None,
        source: str | None = None,
        details: Mapping[str, JsonValue] | None = None,
        code: str | None = None,
    ) -> None:
        context = dict(details or {})
        context.update(
            {
                key: value
                for key, value in {
                    "engine": engine,
                    "engine_version": engine_version,
                    "target_id": target_id,
                    "language": language,
                    "voice": voice,
                    "speaker": speaker,
                    "request_id": request_id,
                    "native_error_type": native_error_type,
                    "amount": amount,
                    "maximum": maximum,
                    "unit": unit,
                    "text_length": text_length,
                    "source": source,
                }.items()
                if value is not None
            }
        )
        super().__init__(message, details=context, code=code)
        self.engine = engine
        self.engine_version = engine_version
        self.target_id = target_id
        self.language = language
        self.voice = voice
        self.speaker = speaker
        self.request_id = request_id
        self.native_error_type = native_error_type
        self.amount = amount
        self.maximum = maximum
        self.unit = unit
        self.text_length = text_length
        self.source = source


class EmptySpeechTextError(EngineSynthesisError):
    code = "synthesis.empty_text"


class InvalidEngineModelError(EngineSynthesisError):
    code = "synthesis.invalid_model"


class InvalidEngineVoiceError(EngineSynthesisError):
    code = "synthesis.invalid_voice"


class InvalidEngineSpeakerError(EngineSynthesisError):
    code = "synthesis.invalid_speaker"


class InvalidEngineLanguageError(EngineSynthesisError):
    code = "synthesis.invalid_language"


class InvalidEngineOptionError(EngineSynthesisError):
    code = "synthesis.invalid_option"


class InvalidSpeechRequestError(EngineSynthesisError):
    code = "synthesis.invalid_request"


class SpeechRequestTooLongError(EngineSynthesisError):
    code = "synthesis.request_too_long"


class UnsupportedSynthesisFeatureError(EngineSynthesisError):
    code = "synthesis.unsupported_feature"


class EngineBackendError(EngineSynthesisError):
    code = "synthesis.backend_error"


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
