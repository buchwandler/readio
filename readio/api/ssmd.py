"""SSMD consumer checks and analysis service."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from .. import config as config_internal
from .. import ssmd as ssmd_internal
from ..document import InputDocument, document_from_file
from ..errors import ReadioError, VoiceResolutionError
from ..jsonutil import JsonValue, json_value
from ..role_targets import engine_for_ssmd_namespace
from ..synthesis import resolve_synthesis_request
from . import errors as api_errors
from .types import (
    Diagnostic,
    Document,
    SSMDAnalysis,
    SSMDCheckResult,
    SSMDVoiceReference,
    SynthesisRequest,
)

if TYPE_CHECKING:
    from .app import Readio


class SSMDService:
    """Check SSMD documents for Readio consumption and role resolution."""

    def __init__(self, app: Readio) -> None:
        self._app = app

    def check(
        self,
        document: Document | Path,
        *,
        synthesis: SynthesisRequest | None = None,
        bindings: Mapping[str, str] | None = None,
    ) -> SSMDCheckResult:
        source = self._document(document)
        analysis = self._analyze(source, synthesis, bindings)
        return SSMDCheckResult(source_path=source.source_path, analysis=analysis)

    def validate(
        self,
        document: Document | Path,
        *,
        synthesis: SynthesisRequest | None = None,
        bindings: Mapping[str, str] | None = None,
    ) -> SSMDCheckResult:
        """Return a check result or raise when the document has unresolved voices."""
        result = self.check(document, synthesis=synthesis, bindings=bindings)
        self._raise_for_unresolved(result.analysis)
        return result

    def _raise_for_unresolved(self, analysis: SSMDAnalysis) -> None:
        unresolved = set(analysis.unresolved_references)
        if not unresolved:
            return
        references = tuple(
            item for item in analysis.voice_references if item.reference in unresolved
        )
        available = tuple(
            sorted(
                {
                    target.voice
                    for target in config_internal.role_targets(
                        self._app.config, engine_for_ssmd_namespace(analysis.provider)
                    ).values()
                }
            )
        )
        header_template = {
            "voice_bindings": {analysis.provider: {item.reference: None for item in references}}
        }
        message = (
            f"cannot resolve {len(references)} SSMD voice reference"
            f"{'s' if len(references) != 1 else ''} for provider {analysis.provider!r}\n"
            + "\n".join(f"  {item.reference} ({item.count} uses)" for item in references)
            + "\n\nAdd document-local bindings:\n  voice_bindings:\n"
            + f"    {analysis.provider}:\n"
            + "".join(f"      {item.reference}: <voice-id>\n" for item in references)
            + "\nRun `readio voices list` to inspect available voices."
        )
        raise VoiceResolutionError(
            message,
            provider=analysis.provider,
            reference=references[0].reference,
            references=references,
            available_voices=available,
            header_template=header_template,
            source_path=analysis.source_path,
        )

    def analyze(self, document: Document | Path) -> SSMDAnalysis:
        return self._analyze(self._document(document), None)

    def _document(self, document: Document | Path) -> InputDocument:
        try:
            if isinstance(document, Path):
                resolved = document_from_file(
                    document,
                    input_format=(
                        "ssmd"
                        if document.name.casefold().endswith((".ssmd", ".ssmd.md"))
                        else "auto"
                    ),
                )
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
