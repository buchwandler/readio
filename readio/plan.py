"""Explicit synthesis plan resolution for Readio.

This module defines the plan domain: request objects, plan dataclasses,
provenance tracking, diagnostics, and the single ``resolve_plan()`` resolver
that produces a ``ReadioPlan`` before any TTS loading occurs.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from . import __version__
from .config import ReadioConfig, language_profile, normalize_language_key
from .document import InputDocument, InputFormat, InputFormatRequest, resolve_input_format
from .formats import (
    AudioFormat,
    audio_format_available,
    format_suffix,
    resolve_audio_format,
)
from .markdown import markdown_to_speech
from .models import get_model_info, language_matches

if TYPE_CHECKING:
    from .synthesis import ResolvedSynthesis

# ---------------------------------------------------------------------------
# Origin vocabulary (stable machine-readable strings)
# ---------------------------------------------------------------------------

ORIGIN_CLI = "cli"
ORIGIN_CONFIG_READER = "config.reader"
ORIGIN_CONFIG_LANGUAGE_EXACT = "config.language.exact"
ORIGIN_CONFIG_LANGUAGE_BASE = "config.language.base"
ORIGIN_CONFIG_VOICE_ROLE = "config.voice_role"
ORIGIN_DOCUMENT = "document"
ORIGIN_DIRECT = "direct"
ORIGIN_PYKOKORO_AUTO = "pykokoro.auto"
ORIGIN_MODEL_DEFAULT = "model.default"
ORIGIN_READIO_DEFAULT = "readio.default"
ORIGIN_INFERRED = "inferred"
ORIGIN_GENERATED = "generated"


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanDiagnostic:
    """A structured diagnostic emitted during plan resolution."""

    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    field: str | None = None
    source_path: Path | None = None
    line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }
        if self.field is not None:
            d["field"] = self.field
        if self.source_path is not None:
            d["source_path"] = str(self.source_path)
        if self.line is not None:
            d["line"] = self.line
        return d


# Diagnostic code constants
DIAG_MODEL_NOT_FOUND = "model_not_found"
DIAG_MODEL_LANGUAGE_INCOMPATIBLE = "model_language_incompatible"
DIAG_MODEL_RUNTIME_UNAVAILABLE = "model_runtime_unavailable"
DIAG_SYNTHESIS_INCOMPLETE = "synthesis_incomplete"
DIAG_QUALITY_UNAVAILABLE = "quality_unavailable"
DIAG_VOICE_UNAVAILABLE = "voice_unavailable"
DIAG_LEXICON_UNAVAILABLE = "lexicon_unavailable"
DIAG_EXPERIMENTAL_FRONTEND_DISALLOWED = "experimental_frontend_disallowed"
DIAG_SSMD_UNRESOLVED_VOICE = "ssmd_unresolved_voice"
DIAG_SSMD_VOICE_UNAVAILABLE = "ssmd_voice_unavailable"
DIAG_OUTPUT_FORMAT_CONFLICT = "output_format_conflict"
DIAG_ENCODER_UNAVAILABLE = "encoder_unavailable"
DIAG_OUTPUT_EXISTS = "output_exists"


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolutionDecision:
    """Records why a particular synthesis field received its value."""

    field: str
    value: object
    origin: str
    locator: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "field": self.field,
            "value": self.value,
            "origin": self.origin,
        }
        if self.locator is not None:
            d["locator"] = self.locator
        if self.reason is not None:
            d["reason"] = self.reason
        return d


# ---------------------------------------------------------------------------
# Language profile plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LanguageProfilePlan:
    """Describes how the language profile was matched."""

    requested: str
    matched: str | None
    match: Literal["exact", "base", "none"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "matched": self.matched,
            "match": self.match,
        }


# ---------------------------------------------------------------------------
# Request objects (decoupled from argparse)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SynthesisRequest:
    """User/request-level synthesis preferences."""

    language: str | None = None
    model: str | None = None
    model_source: str | None = None
    quality: str | None = None
    voice: str | None = None
    lexicons: tuple[str, ...] | None = None
    clear_lexicons: bool = False
    allow_experimental: bool = False
    speed: float | None = None
    pause_mode: str | None = None
    unit: str | None = None
    offline: bool = False
    refresh: bool = False


@dataclass(frozen=True, slots=True)
class InputRequest:
    """Input document specification."""

    document: InputDocument
    requested_format: InputFormatRequest = "auto"
    selector: str = "all"
    source_kind: Literal["literal", "stdin", "file"] | None = None


@dataclass(frozen=True, slots=True)
class OutputRequest:
    """Output specification."""

    mode: Literal["playback", "file"] = "file"
    requested_format: str | None = None
    requested_path: Path | None = None
    force: bool = False


@dataclass(frozen=True, slots=True)
class PlanRequest:
    """Top-level plan request, independent of argparse."""

    operation: Literal["speak", "render"]
    input: InputRequest
    synthesis: SynthesisRequest = field(default_factory=SynthesisRequest)
    output: OutputRequest = field(default_factory=OutputRequest)
    voice_bindings: Mapping[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Plan data objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InputPlan:
    """Resolved input information."""

    source_path: Path | None
    source_kind: str
    requested_format: str
    format: InputFormat
    source_sha256: str
    selector: str
    projected_sha256: str | None = None
    projected_paragraphs: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "source_path": str(self.source_path) if self.source_path else None,
            "source_kind": self.source_kind,
            "requested_format": self.requested_format,
            "format": self.format,
            "source_sha256": self.source_sha256,
            "selector": self.selector,
        }
        if self.projected_sha256 is not None:
            d["projected_sha256"] = self.projected_sha256
        if self.projected_paragraphs is not None:
            d["projected_paragraphs"] = self.projected_paragraphs
        return d


@dataclass(frozen=True, slots=True)
class ModelPlan:
    """Resolved model information."""

    id: str
    source: str
    quality: str
    voice: str
    status: str
    runtime_available: bool
    languages: tuple[str, ...]
    experimental: bool
    distribution_id: str | None = None
    provider: str | None = None
    frontend: str | None = None
    g2p_backend: str | None = None
    sample_rate: int | None = None
    max_tokens: int | None = None
    available_voices: tuple[str, ...] = ()
    available_qualities: tuple[str, ...] = ()
    available_lexicons: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "quality": self.quality,
            "voice": self.voice,
            "status": self.status,
            "runtime_available": self.runtime_available,
            "languages": list(self.languages),
            "experimental": self.experimental,
            "distribution_id": self.distribution_id,
            "provider": self.provider,
            "frontend": self.frontend,
            "g2p_backend": self.g2p_backend,
            "sample_rate": self.sample_rate,
            "max_tokens": self.max_tokens,
            "available_voices": list(self.available_voices),
            "available_qualities": list(self.available_qualities),
            "available_lexicons": (
                list(self.available_lexicons) if self.available_lexicons is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class SynthesisPlan:
    """Resolved synthesis parameters."""

    engine: str
    language: str
    language_profile: LanguageProfilePlan
    model: ModelPlan
    lexicons: tuple[str, ...] | None
    allow_experimental: bool
    speed: float
    pause_mode: str
    unit: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "language": self.language,
            "language_profile": self.language_profile.to_dict(),
            "model": self.model.to_dict(),
            "lexicons": list(self.lexicons) if self.lexicons is not None else None,
            "allow_experimental": self.allow_experimental,
            "speed": self.speed,
            "pause_mode": self.pause_mode,
            "unit": self.unit,
        }


@dataclass(frozen=True, slots=True)
class VoiceBindingPlan:
    """A resolved SSMD voice binding."""

    reference: str
    voice: str
    origin: str
    locator: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "reference": self.reference,
            "voice": self.voice,
            "origin": self.origin,
        }
        if self.locator is not None:
            d["locator"] = self.locator
        return d


@dataclass(frozen=True, slots=True)
class SSMDPlan:
    """Resolved SSMD cast information."""

    enabled: bool
    provider: str | None
    bindings: tuple[VoiceBindingPlan, ...]
    unresolved: tuple[str, ...]
    marker_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "bindings": [b.to_dict() for b in self.bindings],
            "unresolved": list(self.unresolved),
            "marker_source": self.marker_source,
        }


@dataclass(frozen=True, slots=True)
class OutputPlan:
    """Resolved output parameters."""

    mode: Literal["playback", "file"]
    format: str | None
    encoder_backend: str | None
    path: Path | None
    path_origin: Literal["explicit", "generated", "none"]
    force: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "format": self.format,
            "encoder_backend": self.encoder_backend,
            "path": str(self.path) if self.path else None,
            "path_origin": self.path_origin,
            "force": self.force,
        }


@dataclass(frozen=True, slots=True)
class EnvironmentPlan:
    """Environment version information."""

    readio_version: str
    pykokoro_version: str
    ssmd_version: str
    ffmpeg_available: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "readio_version": self.readio_version,
            "pykokoro_version": self.pykokoro_version,
            "ssmd_version": self.ssmd_version,
            "ffmpeg_available": self.ffmpeg_available,
        }


@dataclass(frozen=True, slots=True)
class ReadioPlan:
    """The top-level resolved plan for a Readio job."""

    schema: str
    ok: bool
    operation: Literal["speak", "render"]
    input: InputPlan
    synthesis: SynthesisPlan | None
    ssmd: SSMDPlan
    output: OutputPlan
    environment: EnvironmentPlan
    decisions: tuple[ResolutionDecision, ...]
    diagnostics: tuple[PlanDiagnostic, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "ok": self.ok,
            "operation": self.operation,
            "input": self.input.to_dict(),
            "synthesis": self.synthesis.to_dict() if self.synthesis is not None else None,
            "ssmd": self.ssmd.to_dict(),
            "output": self.output.to_dict(),
            "environment": self.environment.to_dict(),
            "decisions": [d.to_dict() for d in self.decisions],
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }

    def to_json_dict(self) -> dict[str, Any]:
        """Return the JSON-serializable plan dictionary."""
        return self.to_dict()


# ---------------------------------------------------------------------------
# Internal candidate (pre-validation)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SynthesisCandidate:
    """Intermediate synthesis state before backend concretization and validation."""

    language: str
    profile_key: str | None
    profile_match: Literal["exact", "base", "none"]
    model: str | None
    source: str | None
    quality: str | None
    voice: str | None
    lexicons: tuple[str, ...] | None
    allow_experimental: bool
    speed: float
    pause_mode: str
    unit: str
    decisions: tuple[ResolutionDecision, ...]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _package_version(distribution: str) -> str:
    try:
        from importlib.metadata import version

        return version(distribution)
    except Exception:
        return "unknown"


def _select_preferred_quality(qualities: tuple[str, ...]) -> str | None:
    if not qualities:
        return None
    return "fp32" if "fp32" in qualities else qualities[0]


# ---------------------------------------------------------------------------
# Stage 1 — Input planning
# ---------------------------------------------------------------------------


def _plan_input(
    request: InputRequest,
    cfg: ReadioConfig,
) -> tuple[InputPlan, InputDocument, list[PlanDiagnostic]]:
    """Resolve input metadata without loading TTS."""
    diagnostics: list[PlanDiagnostic] = []
    doc = request.document

    # Resolve format
    effective_format: InputFormat = resolve_input_format(
        request.requested_format, source_path=doc.source_path
    )

    if request.source_kind is not None:
        source_kind: str = request.source_kind
    else:
        source_kind = "file" if doc.source_path else "stdin" if doc.text else "text"
    source_sha256 = _sha256_text(doc.text)

    projected_sha256: str | None = None
    projected_paragraphs: int | None = None
    effective_doc = doc

    if effective_format == "markdown":
        try:
            projected_text = markdown_to_speech(doc.text)
            projected_sha256 = _sha256_text(projected_text)
            projected_paragraphs = projected_text.count("\n\n") + 1 if projected_text.strip() else 0
            effective_doc = InputDocument(
                text=projected_text, source_path=doc.source_path, format="text"
            )
        except Exception as exc:
            diagnostics.append(
                PlanDiagnostic(
                    code="input_markdown_parse_error",
                    severity="error",
                    message=f"Failed to parse Markdown: {exc}",
                    source_path=doc.source_path,
                )
            )

    input_plan = InputPlan(
        source_path=doc.source_path,
        source_kind=source_kind,
        requested_format=request.requested_format,
        format=effective_format,
        source_sha256=source_sha256,
        selector=request.selector,
        projected_sha256=projected_sha256,
        projected_paragraphs=projected_paragraphs,
    )

    return input_plan, effective_doc, diagnostics


# ---------------------------------------------------------------------------
# Stage 2 — Readio synthesis policy
# ---------------------------------------------------------------------------


def _resolve_synthesis_candidate(
    cfg: ReadioConfig,
    request: SynthesisRequest,
) -> SynthesisCandidate:
    """Apply Readio precedence rules and record provenance."""
    decisions: list[ResolutionDecision] = []

    # Language
    cli_language = request.language
    raw_language = normalize_language_key(cli_language or cfg.reader.lang)
    profile_key, profile = language_profile(cfg, raw_language)

    if cli_language is not None:
        lang_origin = ORIGIN_CLI
        lang_locator = "request.language"
    else:
        lang_origin = ORIGIN_CONFIG_READER
        lang_locator = "reader.lang"

    decisions.append(
        ResolutionDecision(
            field="synthesis.language",
            value=raw_language,
            origin=lang_origin,
            locator=lang_locator,
        )
    )

    # Profile match
    if profile is not None:
        if profile_key == raw_language:
            profile_match: Literal["exact", "base", "none"] = "exact"
        else:
            profile_match = "base"
    else:
        profile_match = "none"

    # Profile values
    model = profile.model if profile is not None else None
    source = profile.source if profile is not None else None
    quality = profile.quality if profile is not None else None
    voice = profile.voice if profile is not None else None
    lexicons = profile.lexicons if profile is not None else None
    allow_experimental = profile.allow_experimental if profile is not None else False

    # Record profile provenance
    if profile is not None:
        if model is not None:
            profile_origin = (
                ORIGIN_CONFIG_LANGUAGE_EXACT
                if profile_match == "exact"
                else ORIGIN_CONFIG_LANGUAGE_BASE
            )
            decisions.append(
                ResolutionDecision(
                    field="synthesis.model",
                    value=model,
                    origin=profile_origin,
                    locator=f"languages.{profile_key}.model",
                    reason=(
                        f"{'exact' if profile_match == 'exact' else 'base'}-language "
                        f"profile matched requested locale {raw_language}"
                    ),
                )
            )
        if source is not None:
            decisions.append(
                ResolutionDecision(
                    field="synthesis.source",
                    value=source,
                    origin=(
                        ORIGIN_CONFIG_LANGUAGE_EXACT
                        if profile_match == "exact"
                        else ORIGIN_CONFIG_LANGUAGE_BASE
                    ),
                    locator=f"languages.{profile_key}.source",
                )
            )
        if quality is not None:
            decisions.append(
                ResolutionDecision(
                    field="synthesis.quality",
                    value=quality,
                    origin=(
                        ORIGIN_CONFIG_LANGUAGE_EXACT
                        if profile_match == "exact"
                        else ORIGIN_CONFIG_LANGUAGE_BASE
                    ),
                    locator=f"languages.{profile_key}.quality",
                )
            )
        if voice is not None:
            decisions.append(
                ResolutionDecision(
                    field="synthesis.voice",
                    value=voice,
                    origin=(
                        ORIGIN_CONFIG_LANGUAGE_EXACT
                        if profile_match == "exact"
                        else ORIGIN_CONFIG_LANGUAGE_BASE
                    ),
                    locator=f"languages.{profile_key}.voice",
                )
            )
        if lexicons is not None:
            decisions.append(
                ResolutionDecision(
                    field="synthesis.lexicons",
                    value=list(lexicons),
                    origin=(
                        ORIGIN_CONFIG_LANGUAGE_EXACT
                        if profile_match == "exact"
                        else ORIGIN_CONFIG_LANGUAGE_BASE
                    ),
                    locator=f"languages.{profile_key}.lexicons",
                )
            )

    # allow_experimental is additive (profile OR CLI); record the profile
    # contribution whenever it is the winning source.
    if profile is not None and profile.allow_experimental and not request.allow_experimental:
        decisions.append(
            ResolutionDecision(
                field="synthesis.allow_experimental",
                value=True,
                origin=(
                    ORIGIN_CONFIG_LANGUAGE_EXACT
                    if profile_match == "exact"
                    else ORIGIN_CONFIG_LANGUAGE_BASE
                ),
                locator=f"languages.{profile_key}.allow_experimental",
            )
        )

    # CLI/request overrides (highest precedence)
    if request.model is not None:
        model = request.model
        decisions.append(
            ResolutionDecision(
                field="synthesis.model",
                value=model,
                origin=ORIGIN_CLI,
                locator="request.model",
            )
        )
    if request.model_source is not None:
        source = request.model_source
        decisions.append(
            ResolutionDecision(
                field="synthesis.source",
                value=source,
                origin=ORIGIN_CLI,
                locator="request.model_source",
            )
        )
    if request.quality is not None:
        quality = request.quality
        decisions.append(
            ResolutionDecision(
                field="synthesis.quality",
                value=quality,
                origin=ORIGIN_CLI,
                locator="request.quality",
            )
        )
    if request.voice is not None:
        voice = request.voice
        decisions.append(
            ResolutionDecision(
                field="synthesis.voice",
                value=voice,
                origin=ORIGIN_CLI,
                locator="request.voice",
            )
        )
    if request.lexicons is not None:
        lexicons = request.lexicons
        decisions.append(
            ResolutionDecision(
                field="synthesis.lexicons",
                value=list(lexicons),
                origin=ORIGIN_CLI,
                locator="request.lexicons",
            )
        )
    elif request.clear_lexicons:
        lexicons = None
        decisions.append(
            ResolutionDecision(
                field="synthesis.lexicons",
                value=None,
                origin=ORIGIN_CLI,
                locator="request.clear_lexicons",
            )
        )

    # allow_experimental: additive (profile OR CLI)
    if request.allow_experimental:
        allow_experimental = True
        decisions.append(
            ResolutionDecision(
                field="synthesis.allow_experimental",
                value=True,
                origin=ORIGIN_CLI,
                locator="request.allow_experimental",
            )
        )

    # Speed/pause_mode/unit: CLI > config.reader
    speed = request.speed if request.speed is not None else cfg.reader.speed
    pause_mode = request.pause_mode if request.pause_mode is not None else cfg.reader.pause_mode
    unit = request.unit if request.unit is not None else cfg.reader.unit

    if request.speed is not None:
        decisions.append(
            ResolutionDecision(
                field="synthesis.speed",
                value=speed,
                origin=ORIGIN_CLI,
                locator="request.speed",
            )
        )
    else:
        decisions.append(
            ResolutionDecision(
                field="synthesis.speed",
                value=speed,
                origin=ORIGIN_CONFIG_READER,
                locator="reader.speed",
            )
        )

    if request.pause_mode is not None:
        decisions.append(
            ResolutionDecision(
                field="synthesis.pause_mode",
                value=pause_mode,
                origin=ORIGIN_CLI,
                locator="request.pause_mode",
            )
        )
    else:
        decisions.append(
            ResolutionDecision(
                field="synthesis.pause_mode",
                value=pause_mode,
                origin=ORIGIN_CONFIG_READER,
                locator="reader.pause_mode",
            )
        )

    if request.unit is not None:
        decisions.append(
            ResolutionDecision(
                field="synthesis.unit",
                value=unit,
                origin=ORIGIN_CLI,
                locator="request.unit",
            )
        )
    else:
        decisions.append(
            ResolutionDecision(
                field="synthesis.unit",
                value=unit,
                origin=ORIGIN_CONFIG_READER,
                locator="reader.unit",
            )
        )

    # Global reader voice fallback (only when no model, no CLI lang, no profile)
    if voice is None and model is None and not cli_language and profile is None:
        voice = cfg.reader.voice
        if voice is not None:
            decisions.append(
                ResolutionDecision(
                    field="synthesis.voice",
                    value=voice,
                    origin=ORIGIN_READIO_DEFAULT,
                    locator="reader.voice",
                )
            )

    return SynthesisCandidate(
        language=raw_language,
        profile_key=profile_key,
        profile_match=profile_match,
        model=model,
        source=source,
        quality=quality,
        voice=voice,
        lexicons=lexicons,
        allow_experimental=allow_experimental,
        speed=speed,
        pause_mode=pause_mode,
        unit=unit,
        decisions=tuple(decisions),
    )


# ---------------------------------------------------------------------------
# Stage 3 — Backend concretization
# ---------------------------------------------------------------------------


def _concretize_backend_defaults(
    candidate: SynthesisCandidate,
    *,
    cfg: ReadioConfig,
) -> tuple[SynthesisCandidate, list[PlanDiagnostic]]:
    """Use pykokoro.resolve_pipeline_config for automatic-model path.

    When Readio has NOT selected a concrete model (candidate.model is None),
    we ask PyKokoro what it would choose.  This is the key fix from 01.
    """
    diagnostics: list[PlanDiagnostic] = []
    decisions = list(candidate.decisions)

    if candidate.model is not None:
        # Readio already selected a concrete model — do not override.
        return candidate, diagnostics

    # Automatic path: call PyKokoro's public resolver.
    try:
        from pykokoro import GenerationConfig, PipelineConfig, resolve_pipeline_config
        from pykokoro.tokenizer import TokenizerConfig

        requested_backend = PipelineConfig(
            voice=candidate.voice,
            model_source=candidate.source,
            model_variant=candidate.model,
            model_quality=candidate.quality,
            allow_experimental_frontend=candidate.allow_experimental,
            generation=GenerationConfig(
                lang=candidate.language,
                speed=candidate.speed,
                pause_mode=candidate.pause_mode,
            ),
            tokenizer_config=(
                TokenizerConfig(lexicons=candidate.lexicons)
                if candidate.lexicons is not None
                else None
            ),
        )

        effective_backend = resolve_pipeline_config(requested_backend)

        # Track which fields were automatic before resolution
        model_was_auto = candidate.model is None
        source_was_auto = candidate.source is None
        quality_was_auto = candidate.quality is None
        voice_was_auto = candidate.voice is None

        # Extract concrete values
        new_model = effective_backend.model_variant
        new_source = effective_backend.model_source
        new_quality = effective_backend.model_quality
        new_voice = effective_backend.voice

        # Record provenance for fields that were automatic
        if model_was_auto and new_model is not None:
            decisions.append(
                ResolutionDecision(
                    field="synthesis.model",
                    value=new_model,
                    origin=ORIGIN_PYKOKORO_AUTO,
                    reason="model was automatic before PyKokoro pipeline resolution",
                )
            )
        if source_was_auto and new_source is not None:
            decisions.append(
                ResolutionDecision(
                    field="synthesis.source",
                    value=new_source,
                    origin=ORIGIN_PYKOKORO_AUTO,
                    reason="source was automatic before PyKokoro pipeline resolution",
                )
            )
        if quality_was_auto and new_quality is not None:
            decisions.append(
                ResolutionDecision(
                    field="synthesis.quality",
                    value=new_quality,
                    origin=ORIGIN_PYKOKORO_AUTO,
                    reason="quality was automatic before PyKokoro pipeline resolution",
                )
            )
        if voice_was_auto and new_voice is not None:
            decisions.append(
                ResolutionDecision(
                    field="synthesis.voice",
                    value=new_voice,
                    origin=ORIGIN_PYKOKORO_AUTO,
                    reason="voice was automatic before PyKokoro pipeline resolution",
                )
            )

        candidate = SynthesisCandidate(
            language=candidate.language,
            profile_key=candidate.profile_key,
            profile_match=candidate.profile_match,
            model=new_model,
            source=new_source,
            quality=new_quality,
            voice=new_voice,
            lexicons=candidate.lexicons,
            allow_experimental=candidate.allow_experimental,
            speed=candidate.speed,
            pause_mode=candidate.pause_mode,
            unit=candidate.unit,
            decisions=tuple(decisions),
        )

    except Exception as exc:
        diagnostics.append(
            PlanDiagnostic(
                code="backend_resolution_failed",
                severity="error",
                message=f"PyKokoro pipeline resolution failed: {exc}",
                field="synthesis.model",
            )
        )

    return candidate, diagnostics


# ---------------------------------------------------------------------------
# Stage 4 — Capability validation
# ---------------------------------------------------------------------------


def _validate_and_build_model_plan(
    candidate: SynthesisCandidate,
    *,
    offline: bool,
    refresh: bool,
    cfg: ReadioConfig,
) -> tuple[ModelPlan | None, list[PlanDiagnostic], list[ResolutionDecision]]:
    """Validate the concrete model and build ModelPlan."""
    diagnostics: list[PlanDiagnostic] = []
    decisions = list(candidate.decisions)

    if candidate.model is None:
        diagnostics.append(
            PlanDiagnostic(
                code=DIAG_SYNTHESIS_INCOMPLETE,
                severity="error",
                message="synthesis plan is not concrete: no model was resolved",
                field="synthesis.model",
            )
        )
        return None, diagnostics, decisions

    try:
        discovered, _result = get_model_info(
            candidate.model,
            offline=offline,
            refresh=refresh,
            preference=candidate.source or "auto",
        )
    except Exception as exc:
        diagnostics.append(
            PlanDiagnostic(
                code=DIAG_MODEL_NOT_FOUND,
                severity="error",
                message=f"Model {candidate.model!r} not found: {exc}",
                field="synthesis.model",
            )
        )
        return None, diagnostics, decisions

    # Language compatibility: reuse the model-layer matching semantics.
    if not language_matches(candidate.language, discovered.languages):
        declared = ", ".join(discovered.languages) or "none"
        diagnostics.append(
            PlanDiagnostic(
                code=DIAG_MODEL_LANGUAGE_INCOMPATIBLE,
                severity="error",
                message=(
                    f"Model {candidate.model!r} does not declare language "
                    f"{candidate.language!r}. Declared languages: {declared}"
                ),
                field="synthesis.model",
            )
        )

    # Runtime availability: a discovered-but-unavailable model must not plan ok.
    if not discovered.runtime_available:
        diagnostics.append(
            PlanDiagnostic(
                code=DIAG_MODEL_RUNTIME_UNAVAILABLE,
                severity="error",
                message=(
                    f"Model {candidate.model!r} is not available in the installed runtime "
                    f"(status: {discovered.status})."
                ),
                field="synthesis.model",
            )
        )

    # Fill source from discovery when absent, recording the decision.
    effective_source = candidate.source or discovered.source
    if candidate.source is None and discovered.source:
        decisions.append(
            ResolutionDecision(
                field="synthesis.source",
                value=discovered.source,
                origin=ORIGIN_MODEL_DEFAULT,
                locator=f"model.{discovered.id}.source",
                reason="discovered distribution source filled an unspecified request source",
            )
        )

    # Select default voice when voice is absent
    effective_voice = candidate.voice
    if effective_voice is None:
        effective_voice = discovered.default_voice
        decisions.append(
            ResolutionDecision(
                field="synthesis.voice",
                value=effective_voice,
                origin=ORIGIN_MODEL_DEFAULT,
                locator=f"model.{candidate.model}.default_voice",
            )
        )

    # Select preferred quality when absent
    effective_quality = candidate.quality
    if effective_quality is None:
        effective_quality = _select_preferred_quality(discovered.qualities)
        decisions.append(
            ResolutionDecision(
                field="synthesis.quality",
                value=effective_quality,
                origin=ORIGIN_MODEL_DEFAULT,
                locator=f"model.{candidate.model}.qualities",
            )
        )

    # A valid render/speak synthesis plan is concrete: no silent empty values.
    for field_name, value in (
        ("model", candidate.model),
        ("source", effective_source),
        ("quality", effective_quality),
        ("voice", effective_voice),
    ):
        if not value:
            diagnostics.append(
                PlanDiagnostic(
                    code=DIAG_SYNTHESIS_INCOMPLETE,
                    severity="error",
                    message=f"synthesis plan is not concrete: {field_name} is empty",
                    field=f"synthesis.{field_name}",
                )
            )

    # Validate voice is available for this model
    if effective_voice and effective_voice not in discovered.voices:
        diagnostics.append(
            PlanDiagnostic(
                code=DIAG_VOICE_UNAVAILABLE,
                severity="error",
                message=(
                    f"Voice {effective_voice!r} is not available for model {candidate.model!r}. "
                    f"Available: {', '.join(discovered.voices)}"
                ),
                field="synthesis.voice",
            )
        )

    # Validate quality is available
    if effective_quality and effective_quality not in discovered.qualities:
        diagnostics.append(
            PlanDiagnostic(
                code=DIAG_QUALITY_UNAVAILABLE,
                severity="error",
                message=(
                    f"Quality {effective_quality!r} is not available for model {candidate.model!r}. "
                    f"Available: {', '.join(discovered.qualities)}"
                ),
                field="synthesis.quality",
            )
        )

    # Validate lexicons
    if candidate.lexicons is not None and discovered.lexicons is not None:
        for lex in candidate.lexicons:
            if lex not in discovered.lexicons:
                diagnostics.append(
                    PlanDiagnostic(
                        code=DIAG_LEXICON_UNAVAILABLE,
                        severity="error",
                        message=(
                            f"Lexicon {lex!r} is not available for model {candidate.model!r}. "
                            f"Available: {', '.join(discovered.lexicons)}"
                        ),
                        field="synthesis.lexicons",
                    )
                )

    # Validate experimental frontend
    if candidate.allow_experimental and not discovered.experimental:
        # Not an error, just informational
        pass
    elif not candidate.allow_experimental and discovered.experimental:
        diagnostics.append(
            PlanDiagnostic(
                code=DIAG_EXPERIMENTAL_FRONTEND_DISALLOWED,
                severity="error",
                message=(
                    f"Model {candidate.model!r} requires --allow-experimental "
                    "but it was not requested"
                ),
                field="synthesis.allow_experimental",
            )
        )

    model_plan = ModelPlan(
        id=discovered.id,
        source=effective_source or "",
        quality=effective_quality or "",
        voice=effective_voice or "",
        status=discovered.status,
        runtime_available=discovered.runtime_available,
        languages=discovered.languages,
        experimental=discovered.experimental,
        distribution_id=discovered.distribution_id,
        provider=discovered.provider,
        frontend=discovered.frontend,
        g2p_backend=discovered.g2p_backend,
        sample_rate=discovered.sample_rate,
        max_tokens=discovered.max_tokens,
        available_voices=discovered.voices,
        available_qualities=discovered.qualities,
        available_lexicons=discovered.lexicons,
    )

    return model_plan, diagnostics, decisions


# ---------------------------------------------------------------------------
# Stage 5 — SSMD cast planning
# ---------------------------------------------------------------------------


_SSMD_DIAGNOSTIC_CODE_MAP = {
    "ssmd.voice_unavailable": DIAG_SSMD_VOICE_UNAVAILABLE,
    "ssmd.unresolved_voice": DIAG_SSMD_UNRESOLVED_VOICE,
}


def _plan_ssmd(
    input_doc: InputDocument,
    cfg: ReadioConfig,
    *,
    model_plan: ModelPlan | None,
    voice_bindings: Mapping[str, str],
) -> tuple[SSMDPlan, list[PlanDiagnostic], list[ResolutionDecision]]:
    """Resolve the SSMD cast through the shared ``resolve_voice_references``.

    Binding provenance policy (systematic): every executable binding is
    recorded both in ``SSMDPlan.bindings`` and as a ``ssmd.bindings.<ref>``
    decision, so the winning source of each binding is recoverable from the
    plan's ``decisions`` list alone.  Bindings that fail roster validation are
    reported as diagnostics and never marked executable.
    """
    diagnostics: list[PlanDiagnostic] = []
    decisions: list[ResolutionDecision] = []

    if input_doc.format != "ssmd":
        return (
            SSMDPlan(enabled=False, provider=None, bindings=(), unresolved=()),
            diagnostics,
            decisions,
        )

    from .ssmd import SSMDInputError, resolve_voice_references

    provider = cfg.ssmd.voice_provider
    available_voices = model_plan.available_voices if model_plan is not None else None

    try:
        resolved = resolve_voice_references(
            input_doc.text,
            cfg,
            available_voices=available_voices,
            additional_bindings=dict(voice_bindings) if voice_bindings else None,
        )
    except SSMDInputError as exc:
        diagnostics.append(
            PlanDiagnostic(
                code="ssmd_parse_error",
                severity="error",
                message=f"SSMD analysis failed: {exc}",
                source_path=input_doc.source_path,
            )
        )
        return (
            SSMDPlan(enabled=True, provider=provider, bindings=(), unresolved=()),
            diagnostics,
            decisions,
        )

    bindings: list[VoiceBindingPlan] = []
    unresolved_refs: list[str] = []

    for item in resolved:
        has_error = item.diagnostic is not None and item.diagnostic.severity == "error"
        if has_error:
            code = _SSMD_DIAGNOSTIC_CODE_MAP.get(item.diagnostic.code, item.diagnostic.code)
            diagnostics.append(
                PlanDiagnostic(
                    code=code,
                    severity="error",
                    message=item.diagnostic.message,
                    field=f"ssmd.bindings.{item.reference}",
                    source_path=input_doc.source_path,
                    line=item.diagnostic.line,
                )
            )
        if item.voice is None:
            unresolved_refs.append(item.reference)
            if not has_error:
                diagnostics.append(
                    PlanDiagnostic(
                        code=DIAG_SSMD_UNRESOLVED_VOICE,
                        severity="error",
                        message=f"Cannot resolve SSMD voice reference {item.reference!r}.",
                        source_path=input_doc.source_path,
                    )
                )
            continue
        if has_error:
            # A binding target outside the active roster is not executable.
            continue
        bindings.append(
            VoiceBindingPlan(
                reference=item.reference,
                voice=item.voice,
                origin=item.origin,
                locator=item.locator,
            )
        )
        decisions.append(
            ResolutionDecision(
                field=f"ssmd.bindings.{item.reference}",
                value=item.voice,
                origin=item.origin,
                locator=item.locator,
            )
        )

    ssmd_plan = SSMDPlan(
        enabled=True,
        provider=provider,
        bindings=tuple(bindings),
        unresolved=tuple(unresolved_refs),
        marker_source="ssmd",
    )

    return ssmd_plan, diagnostics, decisions


# ---------------------------------------------------------------------------
# Stage 6 — Output planning
# ---------------------------------------------------------------------------


def _plan_output(
    request: OutputRequest,
    cfg: ReadioConfig,
    *,
    input_plan: InputPlan,
) -> tuple[OutputPlan, list[PlanDiagnostic], list[ResolutionDecision]]:
    """Resolve output format, encoder, and path."""
    diagnostics: list[PlanDiagnostic] = []
    decisions: list[ResolutionDecision] = []

    # Resolve format
    try:
        audio_format: AudioFormat = resolve_audio_format(
            requested=request.requested_format,
            output=request.requested_path,
            default="wav",
        )
    except ValueError as exc:
        diagnostics.append(
            PlanDiagnostic(
                code=DIAG_OUTPUT_FORMAT_CONFLICT,
                severity="error",
                message=str(exc),
                field="output.format",
            )
        )
        return (
            OutputPlan(
                mode=request.mode,
                format=None,
                encoder_backend=None,
                path=request.requested_path,
                path_origin="explicit" if request.requested_path else "none",
                force=request.force,
            ),
            diagnostics,
            decisions,
        )

    decisions.append(
        ResolutionDecision(
            field="output.format",
            value=audio_format,
            origin=ORIGIN_INFERRED,
            reason="resolved from explicit format, output suffix, or default",
        )
    )

    # Resolve encoder backend
    from .formats import AUDIO_FORMATS

    spec = AUDIO_FORMATS[audio_format]
    encoder_backend = spec.backend

    if not audio_format_available(audio_format):
        diagnostics.append(
            PlanDiagnostic(
                code=DIAG_ENCODER_UNAVAILABLE,
                severity="error",
                message=(
                    f"Audio format {audio_format!r} requires backend {encoder_backend!r} "
                    "which is not available"
                ),
                field="output.encoder_backend",
            )
        )

    # Resolve output path
    if request.requested_path is not None:
        path = request.requested_path.expanduser()
        if not path.suffix:
            path = path.with_name(path.name + format_suffix(audio_format))
        path_origin: Literal["explicit", "generated", "none"] = "explicit"
        decisions.append(
            ResolutionDecision(
                field="output.path",
                value=str(path),
                origin=ORIGIN_CLI,
                locator="request.requested_path",
            )
        )
    elif request.mode == "file":
        from .paths import resolve_render_output

        path = resolve_render_output(
            cfg,
            explicit=None,
            input_path=input_plan.source_path,
            audio_format=audio_format,
        )
        path_origin = "generated"
        decisions.append(
            ResolutionDecision(
                field="output.path",
                value=str(path),
                origin=ORIGIN_GENERATED,
                reason="automatic output path allocation",
            )
        )
    else:
        path = None
        path_origin = "none"

    # Check if output exists (file mode only)
    if path is not None and path.exists() and not request.force:
        diagnostics.append(
            PlanDiagnostic(
                code=DIAG_OUTPUT_EXISTS,
                severity="warning",
                message=f"Output file already exists: {path}",
                field="output.path",
            )
        )

    output_plan = OutputPlan(
        mode=request.mode,
        format=audio_format,
        encoder_backend=encoder_backend,
        path=path,
        path_origin=path_origin,
        force=request.force,
    )

    return output_plan, diagnostics, decisions


# ---------------------------------------------------------------------------
# Top-level plan resolver
# ---------------------------------------------------------------------------


def resolve_plan(
    cfg: ReadioConfig,
    request: PlanRequest,
) -> ReadioPlan:
    """Resolve a complete ReadioPlan from config and request.

    This is the single plan resolver.  Normal render and dry-run both
    go through this function.
    """
    all_diagnostics: list[PlanDiagnostic] = []
    all_decisions: list[ResolutionDecision] = []

    # Stage 1 — Input planning
    input_plan, effective_doc, input_diags = _plan_input(request.input, cfg)
    all_diagnostics.extend(input_diags)

    # Check for fatal input errors
    has_fatal_input = any(d.severity == "error" for d in input_diags)

    # Stage 2 — Readio synthesis policy
    candidate = _resolve_synthesis_candidate(cfg, request.synthesis)
    all_decisions.extend(candidate.decisions)

    # Stage 3 — Backend concretization
    candidate, concretize_diags = _concretize_backend_defaults(candidate, cfg=cfg)
    all_diagnostics.extend(concretize_diags)

    # Stage 4 — Capability validation
    model_plan, validation_diags, updated_decisions = _validate_and_build_model_plan(
        candidate,
        offline=request.synthesis.offline,
        refresh=request.synthesis.refresh,
        cfg=cfg,
    )
    all_diagnostics.extend(validation_diags)
    all_decisions = list(updated_decisions)

    # Build SynthesisPlan
    synthesis_plan: SynthesisPlan | None = None
    if not has_fatal_input:
        language_profile_plan = LanguageProfilePlan(
            requested=candidate.language,
            matched=candidate.profile_key,
            match=candidate.profile_match,
        )

        if model_plan is not None:
            synthesis_plan = SynthesisPlan(
                engine="pykokoro",
                language=candidate.language,
                language_profile=language_profile_plan,
                model=model_plan,
                lexicons=candidate.lexicons,
                allow_experimental=candidate.allow_experimental,
                speed=candidate.speed,
                pause_mode=candidate.pause_mode,
                unit=candidate.unit,
            )

    # Stage 5 — SSMD cast planning
    ssmd_plan, ssmd_diags, ssmd_decisions = _plan_ssmd(
        effective_doc,
        cfg,
        model_plan=model_plan,
        voice_bindings=request.voice_bindings,
    )
    all_diagnostics.extend(ssmd_diags)
    all_decisions.extend(ssmd_decisions)

    # Stage 6 — Output planning
    output_plan, output_diags, output_decisions = _plan_output(
        request.output,
        cfg,
        input_plan=input_plan,
    )
    all_diagnostics.extend(output_diags)
    all_decisions.extend(output_decisions)

    # Environment
    from .formats import ffmpeg_executable

    environment = EnvironmentPlan(
        readio_version=__version__,
        pykokoro_version=_package_version("pykokoro"),
        ssmd_version=_package_version("ssmd"),
        ffmpeg_available=ffmpeg_executable() is not None,
    )

    # Determine overall ok status
    has_errors = any(d.severity == "error" for d in all_diagnostics)
    has_unresolved_ssmd = len(ssmd_plan.unresolved) > 0
    ok = not has_errors and not has_unresolved_ssmd

    return ReadioPlan(
        schema="readio.plan.v1",
        ok=ok,
        operation=request.operation,
        input=input_plan,
        synthesis=synthesis_plan,
        ssmd=ssmd_plan,
        output=output_plan,
        environment=environment,
        decisions=tuple(all_decisions),
        diagnostics=tuple(all_diagnostics),
    )


# ---------------------------------------------------------------------------
# Compatibility view
# ---------------------------------------------------------------------------


def resolved_synthesis_from_plan(
    plan: ReadioPlan,
) -> ResolvedSynthesis:
    """Derive a ResolvedSynthesis from a ReadioPlan for backward compatibility."""
    from .synthesis import ResolvedSynthesis

    if plan.synthesis is None:
        return ResolvedSynthesis(
            language="en-us",
            model=None,
            source=None,
            quality=None,
            voice=None,
            lexicons=None,
            allow_experimental=False,
            speed=1.0,
            pause_mode="tts",
            unit="sentence",
        )

    sp = plan.synthesis
    return ResolvedSynthesis(
        language=sp.language,
        model=sp.model.id if sp.model else None,
        source=sp.model.source if sp.model else None,
        quality=sp.model.quality if sp.model else None,
        voice=sp.model.voice if sp.model else None,
        lexicons=sp.lexicons,
        allow_experimental=sp.allow_experimental,
        speed=sp.speed,
        pause_mode=sp.pause_mode,
        unit=sp.unit,
    )


# ---------------------------------------------------------------------------
# Plan formatting (human-readable)
# ---------------------------------------------------------------------------


def format_plan_human(plan: ReadioPlan) -> str:
    """Format a plan for human-readable terminal output."""
    lines: list[str] = []

    lines.append("Input")
    lines.append(f"  Source:    {plan.input.source_path or '(stdin)'}")
    lines.append(f"  Format:    {plan.input.format}")
    lines.append(f"  SHA256:    {plan.input.source_sha256[:16]}...")
    if plan.input.projected_sha256:
        lines.append(f"  Projected: {plan.input.projected_sha256[:16]}...")

    if plan.synthesis:
        sp = plan.synthesis
        lines.append("")
        lines.append("Language")
        lines.append(f"  Requested: {sp.language_profile.requested}")
        lines.append(
            f"  Profile:   {sp.language_profile.matched or '(none)'} ({sp.language_profile.match})"
        )

        lines.append("")
        lines.append("Synthesis")
        lines.append(f"  Engine:      {sp.engine}")
        lines.append(f"  Model:       {sp.model.id}")
        lines.append(f"  Model source:{sp.model.source}")
        lines.append(f"  Quality:     {sp.model.quality}")
        lines.append(f"  Voice:       {sp.model.voice}")
        if sp.model.frontend:
            lines.append(f"  Frontend:    {sp.model.frontend}")
        if sp.model.g2p_backend:
            lines.append(f"  G2P:         {sp.model.g2p_backend}")
        if sp.lexicons:
            lines.append(f"  Lexicons:    {', '.join(sp.lexicons)}")
        lines.append(f"  Speed:       {sp.speed}")
        lines.append(f"  Unit:        {sp.unit}")
        if sp.model.sample_rate:
            lines.append(f"  Sample rate: {sp.model.sample_rate} Hz")

    if plan.decisions:
        lines.append("")
        lines.append("Why")
        for d in plan.decisions:
            field_short = d.field.replace("synthesis.", "").replace("output.", "output.")
            locator = d.locator or d.origin
            lines.append(f"  {field_short:<16s} {locator}")

    if plan.ssmd.enabled and plan.ssmd.bindings:
        lines.append("")
        lines.append("SSMD cast")
        for b in plan.ssmd.bindings:
            lines.append(f"  {b.reference:<12s} -> {b.voice}  ({b.origin})")

    if plan.output:
        lines.append("")
        lines.append("Output")
        if plan.output.format:
            lines.append(f"  Format:   {plan.output.format}")
        if plan.output.encoder_backend:
            lines.append(f"  Encoder:  {plan.output.encoder_backend}")
        if plan.output.path:
            lines.append(f"  Path:     {plan.output.path}")
        lines.append(f"  Source:   {plan.output.path_origin}")

    lines.append("")
    lines.append("Environment")
    lines.append(f"  Readio:   {plan.environment.readio_version}")
    lines.append(f"  PyKokoro: {plan.environment.pykokoro_version}")
    lines.append(f"  SSMD:     {plan.environment.ssmd_version}")

    if plan.diagnostics:
        lines.append("")
        lines.append("Diagnostics")
        for d in plan.diagnostics:
            prefix = {"info": "ℹ", "warning": "⚠", "error": "✗"}.get(d.severity, "?")
            lines.append(f"  {prefix} [{d.code}] {d.message}")

    lines.append("")
    if plan.ok:
        lines.append("No TTS model was loaded.")
    else:
        lines.append("Plan has errors. No TTS model was loaded.")

    return "\n".join(lines)


__all__ = [
    "DIAG_ENCODER_UNAVAILABLE",
    "DIAG_EXPERIMENTAL_FRONTEND_DISALLOWED",
    "DIAG_LEXICON_UNAVAILABLE",
    "DIAG_MODEL_LANGUAGE_INCOMPATIBLE",
    "DIAG_MODEL_NOT_FOUND",
    "DIAG_MODEL_RUNTIME_UNAVAILABLE",
    "DIAG_OUTPUT_EXISTS",
    "DIAG_OUTPUT_FORMAT_CONFLICT",
    "DIAG_QUALITY_UNAVAILABLE",
    "DIAG_SSMD_UNRESOLVED_VOICE",
    "DIAG_SSMD_VOICE_UNAVAILABLE",
    "DIAG_SYNTHESIS_INCOMPLETE",
    "DIAG_VOICE_UNAVAILABLE",
    "ORIGIN_CLI",
    "ORIGIN_CONFIG_LANGUAGE_BASE",
    "ORIGIN_CONFIG_LANGUAGE_EXACT",
    "ORIGIN_CONFIG_READER",
    "ORIGIN_CONFIG_VOICE_ROLE",
    "ORIGIN_DIRECT",
    "ORIGIN_DOCUMENT",
    "ORIGIN_GENERATED",
    "ORIGIN_INFERRED",
    "ORIGIN_MODEL_DEFAULT",
    "ORIGIN_PYKOKORO_AUTO",
    "ORIGIN_READIO_DEFAULT",
    "EnvironmentPlan",
    "InputPlan",
    "InputRequest",
    "LanguageProfilePlan",
    "ModelPlan",
    "OutputPlan",
    "OutputRequest",
    "PlanDiagnostic",
    "PlanRequest",
    "ReadioPlan",
    "ResolutionDecision",
    "SSMDPlan",
    "SynthesisPlan",
    "SynthesisRequest",
    "VoiceBindingPlan",
    "format_plan_human",
    "resolve_plan",
    "resolved_synthesis_from_plan",
]
