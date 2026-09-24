"""SSMD consumer checks and authoring helpers."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from .. import ssmd as ssmd_internal
from .. import ssmd_authoring
from ..document import InputDocument, document_from_file
from ..errors import ReadioError
from ..jsonutil import JsonValue, json_value
from ..synthesis import resolve_synthesis_request
from . import errors as api_errors
from .types import (
    Diagnostic,
    Document,
    SSMDAnalysis,
    SSMDCheckResult,
    SSMDMaterializeResult,
    SSMDVoiceReference,
    SynthesisRequest,
)

if TYPE_CHECKING:
    from .app import Readio


class SSMDService:
    """Validate SSMD for consumption and expose safe authoring operations."""

    def __init__(self, app: Readio) -> None:
        self._app = app

    def check(
        self,
        document: Document | Path,
        *,
        roundtrip: bool = False,
        synthesis: SynthesisRequest | None = None,
        bindings: Mapping[str, str] | None = None,
    ) -> SSMDCheckResult:
        source = self._document(document)
        analysis = self._analyze(source, synthesis, bindings)
        roundtrip_result = None
        if roundtrip:
            if source.source_path is None:
                raise api_errors.InvalidRequestError(
                    "roundtrip checking requires a filesystem source path",
                    code="ssmd.roundtrip_requires_path",
                )
            roundtrip_result = self._roundtrip(source.source_path)
        return SSMDCheckResult(
            source_path=source.source_path,
            analysis=analysis,
            roundtrip=roundtrip_result,
        )

    def analyze(self, document: Document | Path) -> SSMDAnalysis:
        return self._analyze(self._document(document), None)

    def materialize_bindings(
        self,
        source: Path,
        bindings: Mapping[str, str],
        *,
        provider: str | None = None,
        output: Path | None = None,
        in_place: bool = False,
    ) -> SSMDMaterializeResult:
        source = source.expanduser()
        provider_id = provider or self._app.config.ssmd.voice_provider
        try:
            output_path = ssmd_authoring.materialize_voice_bindings(
                source,
                bindings,
                provider=provider_id,
                output=output,
                in_place=in_place,
            )
        except ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.InputError,
                code="ssmd.materialize_failed",
                source_path=source,
            ) from error
        return SSMDMaterializeResult(
            source_path=source,
            output_path=output_path,
            provider=provider_id,
            binding_count=len(bindings),
            in_place=in_place,
        )

    def roundtrip_check(self, source: Path) -> SSMDCheckResult:
        return self.check(source, roundtrip=True)

    def _document(self, document: Document | Path) -> InputDocument:
        try:
            if isinstance(document, Path):
                resolved = document_from_file(document)
            elif isinstance(document, InputDocument):
                resolved = document
            else:
                raise api_errors.InvalidRequestError(
                    "document must be an InputDocument or a filesystem path",
                    code="ssmd.invalid_document",
                )
        except ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.InputError,
                code="input.read_failed",
                source_path=document if isinstance(document, Path) else None,
            ) from error
        if resolved.format != "ssmd":
            raise api_errors.InvalidRequestError(
                "SSMD operations require an SSMD document",
                source_path=resolved.source_path,
                code="ssmd.input_format_required",
            )
        return resolved

    def _analyze(
        self,
        document: InputDocument,
        synthesis: SynthesisRequest | None,
        bindings: Mapping[str, str] | None = None,
    ) -> SSMDAnalysis:
        try:
            resolved = (
                resolve_synthesis_request(self._app.config, synthesis)
                if synthesis is not None
                else None
            )
            raw = ssmd_internal.analyze_ssmd(
                document.text,
                self._app.config,
                source_path=document.source_path,
                synthesis=resolved,
                additional_bindings=bindings or {},
            )
        except ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.InputError,
                code="ssmd.analysis_failed",
                source_path=document.source_path,
            ) from error
        return SSMDAnalysis(
            provider=raw.provider,
            source_path=document.source_path,
            document_bindings=raw.document_bindings,
            default_bindings=raw.default_bindings,
            runtime_bindings=raw.runtime_bindings,
            voice_references=tuple(self._reference(item) for item in raw.voice_references),
            unresolved_references=tuple(raw.unresolved_references),
            diagnostics=tuple(self._diagnostic(item) for item in raw.diagnostics),
        )

    def _roundtrip(self, source: Path) -> Mapping[str, JsonValue]:
        try:
            raw = ssmd_authoring.roundtrip_check(source, self._app.config)
            value = json_value(raw)
        except ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.IntegrationError,
                code="ssmd.roundtrip_failed",
                source_path=source,
            ) from error
        if not isinstance(value, dict):
            raise api_errors.IntegrationError(
                "SSMD roundtrip returned an invalid result",
                source_path=source,
                code="ssmd.roundtrip_invalid_result",
            )
        return cast(dict[str, JsonValue], value)

    def _reference(self, raw: object) -> SSMDVoiceReference:
        return SSMDVoiceReference(
            reference=str(getattr(raw, "reference", "")),
            count=int(getattr(raw, "count", 0)),
            lines=tuple(int(line) for line in getattr(raw, "lines", ())),
        )

    def _diagnostic(self, raw: object) -> Diagnostic:
        return Diagnostic(
            code=str(getattr(raw, "code", "ssmd.diagnostic")),
            severity=cast(Literal["info", "warning", "error"], getattr(raw, "severity", "warning")),
            message=str(getattr(raw, "message", raw)),
            line=getattr(raw, "line", None),
            details=cast(
                dict[str, JsonValue],
                json_value(raw.to_dict() if hasattr(raw, "to_dict") else {}),
            ),
        )


__all__ = ["SSMDService"]
