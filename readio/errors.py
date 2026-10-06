from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

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
        renderability_reason: str = "punctuation_only",
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
            "reason": f"{renderability_reason}_renderer_segment",
            "renderability_reason": renderability_reason,
            "renderability_code": f"renderability.{renderability_reason}",
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
            f"speech text is {renderability_reason.replace('_', '-')} {text!r}. "
            "Rebuild the semantic plan with: readio plan build ."
        )
        super().__init__(message, details=context)


class InvalidStoredPlanError(ReadioError):
    """A persisted semantic plan violates the current Utterplan contract."""

    rebuild_command = "readio plan build ."

    def __init__(
        self,
        *,
        scope_id: str,
        scope_path: str,
        artifact_path: Path,
        validation_code: str,
        validation_path: str | None = None,
    ) -> None:
        renderability_invalid = validation_code == "segment.not_renderable"
        code = (
            "plan.invalid.not_renderable"
            if renderability_invalid
            else "plan.artifact.schema_mismatch"
            if validation_code == "schema_mismatch"
            else "plan.artifact.legacy_format"
            if validation_code == "legacy_format"
            else "plan.artifact.invalid"
        )
        message = (
            "Stored semantic plan is invalid under the current renderer contract."
            if renderability_invalid
            else "Stored semantic plan artifact is invalid."
        )
        details: dict[str, JsonValue] = {
            "scope_id": scope_id,
            "scope_path": scope_path,
            "artifact_path": str(artifact_path),
            "validation_code": validation_code,
            "action": self.rebuild_command,
        }
        if validation_path is not None:
            details["validation_path"] = validation_path
        super().__init__(
            f"{message} Rebuild with: {self.rebuild_command}",
            source_path=artifact_path,
            details=details,
            code=code,
        )


class SynthesisPreflightError(ReadioError):
    """Selected synthesis targets cannot render one or more requests safely."""

    code = "synthesis.preflight_failed"

    def __init__(self, issues: tuple[dict[str, JsonValue], ...]) -> None:
        self.issues = tuple(dict(issue) for issue in issues)
        source_path_value = self.issues[0].get("source_path") if self.issues else None
        source_path = Path(source_path_value) if isinstance(source_path_value, str) else None
        details: dict[str, JsonValue] = {
            "issues": cast(JsonValue, list(self.issues)),
            "rendered_new_segments": 0,
            "synthesis_requests": 0,
            "audio_artifacts_written": 0,
        }
        super().__init__(
            f"Synthesis preflight failed: {len(self.issues)} selected segment(s) are not renderable. "
            "Rendered new segments: 0.",
            source_path=source_path,
            details=details,
        )


class ProjectPlanRenderabilityError(ReadioError):
    """One or more project scopes contain unrenderable semantic segments."""

    code = "planning.not_renderable"
    repair_command = "readio plan build . --renderability repair"

    def __init__(
        self,
        issues: tuple[dict[str, JsonValue], ...],
        *,
        renderability_mode: str,
    ) -> None:
        self.issues = tuple(dict(issue) for issue in issues)
        self.renderability_mode = renderability_mode
        source_path_value = self.issues[0].get("source_path") if self.issues else None
        source_path = Path(source_path_value) if isinstance(source_path_value, str) else None
        details: dict[str, JsonValue] = {
            "issues": list(self.issues),
            "renderability_mode": renderability_mode,
            "repair_command": self.repair_command,
        }
        super().__init__(
            f"Planning failed: {len(self.issues)} renderer segment(s) are not renderable.",
            source_path=source_path,
            details=details,
        )


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
