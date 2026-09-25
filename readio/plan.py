"""Explicit synthesis plan resolution for Readio.

This module defines the plan domain: request objects, plan dataclasses,
provenance tracking, diagnostics, and the single ``resolve_plan()`` resolver
that produces a ``ReadioPlan`` before any TTS loading occurs.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from utterplan import CURRENT_SCHEMA_VERSION

from .config import (
    ReadioConfig,
    language_profile,
    normalize_language_key,
    normalize_short_sentence_policy,
    normalize_spacy_policy,
)
from .document import InputDocument, InputFormat, InputFormatRequest, resolve_input_format
from .errors import RenderError
from .formats import (
    AudioFormat,
    audio_format_available,
    format_suffix,
    resolve_audio_format,
)
from .jsonutil import JsonValue
from .markdown import markdown_to_speech
from .voices import resolve_voice_selector

SUPPORTED_UTTERPLAN_SCHEMA_VERSION = 3
if CURRENT_SCHEMA_VERSION != SUPPORTED_UTTERPLAN_SCHEMA_VERSION:
    raise RuntimeError(
        "Readio supports Utterplan schema v3; "
        f"installed Utterplan reports schema {CURRENT_SCHEMA_VERSION}"
    )

logger = logging.getLogger(__name__)
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
    speaker: str | int | None = None
    clear_lexicons: bool = False
    auto_lexicons: bool = False
    spacy: str | None = None
    short_sentence: str | None = None
    g2p_fallback: str | None = None
    lexicon_data_policy: str | None = None
    language_detection: str | None = None
    detect_languages: tuple[str, ...] | None = None
    allow_experimental: bool = False
    speed: float | None = None
    pause_mode: str | None = None
    unit: str | None = None
    offline: bool = False
    refresh: bool = False
    engine: str | None = None
    engine_options: Mapping[str, JsonValue] = field(default_factory=dict)
    voice_file: Path | None = None


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
    bitrate: str | None = None


@dataclass(frozen=True, slots=True)
class CompositionOptions:
    target_lufs: float | None = None
    true_peak_ceiling_dbtp: float = -1.0
    peak_policy: Literal["reduce_gain", "error"] = "reduce_gain"
    clip_policy: Literal["clamp", "warn", "error"] = "clamp"
    sample_rate: int | None = None

    def __post_init__(self) -> None:
        if self.sample_rate is not None and (
            isinstance(self.sample_rate, bool)
            or not isinstance(self.sample_rate, int)
            or self.sample_rate <= 0
        ):
            raise ValueError("composition.sample_rate must be a positive integer")


@dataclass(frozen=True, slots=True)
class PlanRequest:
    """Top-level plan request, independent of argparse."""

    operation: Literal["speak", "render"]
    input: InputRequest
    synthesis: SynthesisRequest = field(default_factory=SynthesisRequest)
    output: OutputRequest = field(default_factory=OutputRequest)
    voice_bindings: Mapping[str, str] = field(default_factory=dict)
    project_voice_bindings: Mapping[str, str] = field(default_factory=dict)
    scope_voice_bindings: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    composition: CompositionOptions = field(default_factory=CompositionOptions)


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
    backend: str = "pykokoro"
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
            "distribution_provider": self.provider,
            "backend": self.backend,
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
    g2p_fallback: str | None
    spacy: str
    short_sentence: str
    lexicon_data_policy: str | None
    language_detection: str | None
    detect_languages: tuple[str, ...] | None
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
            "g2p_fallback": self.g2p_fallback,
            "spacy": self.spacy,
            "short_sentence": self.short_sentence,
            "lexicon_data_policy": self.lexicon_data_policy,
            "language_detection": self.language_detection,
            "detect_languages": (
                list(self.detect_languages) if self.detect_languages is not None else None
            ),
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
    g2p_fallback: str | None
    spacy: str
    short_sentence: str
    lexicon_data_policy: str | None
    language_detection: str | None
    detect_languages: tuple[str, ...] | None
    allow_experimental: bool
    speed: float
    pause_mode: str
    unit: str
    decisions: tuple[ResolutionDecision, ...]
    engine: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _package_version(distribution: str) -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version(distribution)
    except PackageNotFoundError:
        return "unknown"


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
        except (ValueError, KeyError, TypeError) as exc:
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
    document_language_detection: tuple[str, tuple[str, ...]] | None = None,
) -> SynthesisCandidate:
    """Apply Readio precedence rules and record provenance."""
    decisions: list[ResolutionDecision] = []

    selector_resolution = resolve_voice_selector(
        request.voice,
        language=request.language,
        model=request.model,
        source=request.model_source,
        offline=request.offline,
        refresh=request.refresh,
        preference=request.model_source or "auto",
        engine=request.engine,
    )
    requested_selector = (
        request.voice if selector_resolution and selector_resolution.selector else None
    )
    if selector_resolution is not None and selector_resolution.selector is not None:
        request = replace(
            request,
            language=selector_resolution.language,
            model=selector_resolution.model,
            model_source=selector_resolution.source,
            voice=selector_resolution.voice,
            engine=selector_resolution.backend,
        )
        decisions.append(
            ResolutionDecision(
                field="synthesis.voice_selector",
                value=requested_selector,
                origin=ORIGIN_CLI,
                locator="request.voice",
                reason=(
                    f"voice selector {requested_selector!r} resolved via OnnxVoice to "
                    f"engine {selector_resolution.backend!r} / model "
                    f"{selector_resolution.model!r} / voice {selector_resolution.voice!r}"
                ),
            )
        )
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
    engine = profile.engine if profile is not None else None
    quality = profile.quality if profile is not None else None
    voice = profile.voice if profile is not None else None
    lexicons = profile.lexicons if profile is not None else None
    allow_experimental = profile.allow_experimental if profile is not None else False
    g2p_fallback = profile.g2p_fallback if profile is not None else None
    lexicon_data_policy = profile.lexicon_data_policy if profile is not None else None

    if request.engine is not None:
        engine = request.engine
    if engine is None:
        engine = cfg.reader.engine
    spacy = normalize_spacy_policy(request.spacy if request.spacy is not None else cfg.reader.spacy)
    if request.spacy is not None or spacy != "auto":
        decisions.append(
            ResolutionDecision(
                field="synthesis.spacy",
                value=spacy,
                origin=ORIGIN_CLI if request.spacy is not None else ORIGIN_CONFIG_READER,
                locator="request.spacy" if request.spacy is not None else "reader.spacy",
            )
        )
    short_sentence = normalize_short_sentence_policy(
        request.short_sentence if request.short_sentence is not None else cfg.reader.short_sentence
    )
    if request.short_sentence is not None or short_sentence != "auto":
        decisions.append(
            ResolutionDecision(
                field="synthesis.short_sentence",
                value=short_sentence,
                origin=(ORIGIN_CLI if request.short_sentence is not None else ORIGIN_CONFIG_READER),
                locator=(
                    "request.short_sentence"
                    if request.short_sentence is not None
                    else "reader.short_sentence"
                ),
            )
        )
    language_detection = request.language_detection
    detect_languages = request.detect_languages
    detection_origin = (
        ORIGIN_CLI if language_detection is not None or detect_languages is not None else None
    )
    detection_locator = (
        "request.language_detection"
        if language_detection is not None
        else "request.detect_languages"
    )
    if (
        language_detection is None
        and detect_languages is None
        and document_language_detection is not None
    ):
        language_detection, detect_languages = document_language_detection
        detection_origin = ORIGIN_DOCUMENT
        detection_locator = "ssmd.language_detection"
    if language_detection is None and detect_languages is None:
        language_detection = cfg.reader.language_detection
        detect_languages = cfg.reader.detect_languages
        if language_detection is not None or detect_languages is not None:
            detection_origin = ORIGIN_CONFIG_READER
            detection_locator = "reader.language_detection"
    if language_detection is None and detect_languages is not None:
        language_detection = "auto"
    if detection_origin is not None:
        decisions.append(
            ResolutionDecision(
                field="synthesis.language_detection",
                value=language_detection,
                origin=detection_origin,
                locator=detection_locator,
            )
        )
        decisions.append(
            ResolutionDecision(
                field="synthesis.detect_languages",
                value=list(detect_languages) if detect_languages is not None else None,
                origin=detection_origin,
                locator=detection_locator,
            )
        )
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
        if profile.g2p_fallback is not None:
            decisions.append(
                ResolutionDecision(
                    field="synthesis.g2p_fallback",
                    value=profile.g2p_fallback,
                    origin=ORIGIN_CONFIG_LANGUAGE_EXACT
                    if profile_match == "exact"
                    else ORIGIN_CONFIG_LANGUAGE_BASE,
                    locator=f"languages.{profile_key}.g2p_fallback",
                )
            )
        if profile.lexicon_data_policy is not None:
            decisions.append(
                ResolutionDecision(
                    field="synthesis.lexicon_data_policy",
                    value=profile.lexicon_data_policy,
                    origin=ORIGIN_CONFIG_LANGUAGE_EXACT
                    if profile_match == "exact"
                    else ORIGIN_CONFIG_LANGUAGE_BASE,
                    locator=f"languages.{profile_key}.lexicon_data_policy",
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
    if request.g2p_fallback is not None:
        g2p_fallback = request.g2p_fallback
        decisions.append(
            ResolutionDecision(
                field="synthesis.g2p_fallback",
                value=g2p_fallback,
                origin=ORIGIN_CLI,
                locator="request.g2p_fallback",
            )
        )
    if request.lexicon_data_policy is not None:
        lexicon_data_policy = request.lexicon_data_policy
        decisions.append(
            ResolutionDecision(
                field="synthesis.lexicon_data_policy",
                value=lexicon_data_policy,
                origin=ORIGIN_CLI,
                locator="request.lexicon_data_policy",
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
        lexicons = ()
        decisions.append(
            ResolutionDecision(
                field="synthesis.lexicons",
                value=[],
                origin=ORIGIN_CLI,
                locator="request.clear_lexicons",
                reason="explicitly disable static lexicon layers",
            )
        )
    elif request.auto_lexicons:
        lexicons = None
        decisions.append(
            ResolutionDecision(
                field="synthesis.lexicons",
                value=None,
                origin=ORIGIN_CLI,
                locator="request.auto_lexicons",
                reason="use PyKokoro/KokoroG2P language defaults",
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
    # A global reader voice may only cross into the selected engine when the
    # request did not explicitly switch engines.
    from .engines.registry import normalize_engine_id

    selected_engine = normalize_engine_id(engine or cfg.reader.engine)
    reader_engine = normalize_engine_id(cfg.reader.engine)
    if (
        voice is None
        and model is None
        and not cli_language
        and profile is None
        and (request.engine is None or selected_engine == reader_engine)
    ):
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
        g2p_fallback=g2p_fallback,
        spacy=spacy,
        short_sentence=short_sentence,
        lexicon_data_policy=lexicon_data_policy,
        language_detection=language_detection,
        detect_languages=detect_languages,
        allow_experimental=allow_experimental,
        speed=speed,
        pause_mode=pause_mode,
        unit=unit,
        decisions=tuple(decisions),
        engine=engine,
    )


# ---------------------------------------------------------------------------

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
                severity="error",
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


def resolve_plan(cfg: ReadioConfig, request: PlanRequest) -> ReadioPlanV2:
    """Resolve the engine-neutral v2 plan contract."""
    return resolve_plan_v2(cfg, request)


# ---------------------------------------------------------------------------
# Plan v2 resolver
# ---------------------------------------------------------------------------


def _resolve_v2_ssmd_roles(
    semantic: Any,
    cfg: ReadioConfig,
    request: PlanRequest,
    provider: str,
    adapter: Any,
    selection: Any,
    default_target: RenderTargetV2,
    source_path: Path | None,
) -> tuple[
    tuple[RoleTargetBindingV2, ...], tuple[VoiceBindingPlan, ...], list[str], list[PlanDiagnostic]
]:
    """Resolve SSMD roles from the semantic plan without reparsing its source."""
    references: dict[str, str] = {}
    for segment in semantic.plan.segments:
        directive = segment.directives.voice
        if directive is not None and directive.reference:
            references.setdefault(directive.reference, segment.id)

    metadata = semantic.plan.document_metadata
    raw_document_bindings = metadata.get("voice_bindings", {})
    document_bindings = (
        raw_document_bindings.get(provider, {})
        if isinstance(raw_document_bindings, Mapping)
        else {}
    )
    document_bindings = document_bindings if isinstance(document_bindings, Mapping) else {}
    invocation_bindings = dict(request.voice_bindings)
    project_bindings = dict(request.project_voice_bindings)
    voice_settings = cfg.voices.get(provider)
    configured_roles = voice_settings.roles if voice_settings is not None else {}
    target_metadata = getattr(adapter, "target_metadata", lambda _selection: {})(selection)
    available = set(target_metadata.get("voices", ()))
    if voice_settings is not None:
        available.update(voice_settings.ids)
    if adapter.capabilities().voice_binding_scope == "target":
        available.add(selection.target_id)

    bindings: list[VoiceBindingPlan] = []
    role_targets: list[RoleTargetBindingV2] = []
    unresolved: list[str] = []
    diagnostics: list[PlanDiagnostic] = []
    for reference, locator in references.items():
        origin: str | None = None
        selected_voice: str | None = None
        selected_locator: str | None = locator
        if reference in document_bindings:
            selected_voice = document_bindings[reference]
            origin = ORIGIN_DOCUMENT
            selected_locator = "utterplan.document_metadata.voice_bindings"
        elif reference in invocation_bindings:
            selected_voice = invocation_bindings[reference]
            origin = ORIGIN_CLI
            selected_locator = "request.voice_bindings"
        elif reference in project_bindings:
            selected_voice = project_bindings[reference]
            origin = "project"
            selected_locator = f"project.settings.ssmd.voice_bindings.{provider}.{reference}"
        elif reference in configured_roles:
            selected_voice = configured_roles[reference]
            origin = ORIGIN_CONFIG_VOICE_ROLE
            selected_locator = f"voices.{provider}.roles.{reference}"
        elif reference in available:
            selected_voice = reference
            origin = "direct"
            selected_locator = None

        if selected_voice is None:
            unresolved.append(reference)
            diagnostics.append(
                PlanDiagnostic(
                    code=DIAG_SSMD_UNRESOLVED_VOICE,
                    severity="error",
                    message=f"Cannot resolve SSMD voice reference {reference!r}.",
                    field=f"ssmd.bindings.{reference}",
                    source_path=source_path,
                )
            )
            continue
        if selected_voice not in available:
            diagnostics.append(
                PlanDiagnostic(
                    code=DIAG_SSMD_VOICE_UNAVAILABLE,
                    severity="error",
                    message=f"SSMD role {reference!r} resolves to unavailable voice {selected_voice!r}.",
                    field=f"ssmd.bindings.{reference}",
                    source_path=source_path,
                )
            )
            continue

        binding = VoiceBindingPlan(
            reference=reference,
            voice=selected_voice,
            origin=origin or "direct",
            locator=selected_locator,
        )
        bindings.append(binding)
        decisions_locator = selected_locator or f"ssmd.voice.{reference}"
        if adapter.capabilities().voice_binding_scope == "target":
            role_selection = replace(selection, target_id=selected_voice)
            role_metadata = getattr(adapter, "target_metadata", lambda _selection: {})(
                role_selection
            )
            role_target = RenderTargetV2(
                id=selected_voice,
                language=default_target.language,
                speaker=default_target.speaker,
                options=default_target.options,
                metadata=role_metadata,
            )
        else:
            role_target = RenderTargetV2(
                id=default_target.id,
                language=default_target.language,
                voice=VoiceSourceV2(kind="named", value=selected_voice),
                speaker=default_target.speaker,
                options=default_target.options,
                metadata=default_target.metadata,
            )
        role_targets.append(
            RoleTargetBindingV2(
                role=reference,
                target=role_target,
                origin=origin or "direct",
                locator=decisions_locator,
            )
        )
    return tuple(role_targets), tuple(bindings), unresolved, diagnostics


def resolve_execution_v2(cfg: ReadioConfig, request: PlanRequest) -> Any:
    """Resolve a serializable v2 plan and its in-memory execution artifacts."""
    from .engines.base import EngineSelection
    from .engines.registry import get_engine, normalize_engine_id
    from .engines.selection import EngineRequest
    from .execution import ResolvedExecutionV2
    from .formats import ffmpeg_executable
    from .planning import PlanningPolicy, compile_semantic_plan
    from .planning.compiler import CompiledSemanticPlan

    input_plan, effective_doc, input_diags = _plan_input(request.input, cfg)
    diagnostics: list[PlanDiagnostic] = list(input_diags)
    decisions: list[ResolutionDecision] = []

    document_language_detection: tuple[str, tuple[str, ...]] | None = None
    candidate = _resolve_synthesis_candidate(cfg, request.synthesis, document_language_detection)
    decisions.extend(candidate.decisions)
    engine_id = normalize_engine_id(candidate.engine or cfg.reader.engine)
    adapter = None
    selection: EngineSelection | None = None

    adapter_api_compatible = True
    try:
        adapter = get_engine(engine_id)
    except (ImportError, ValueError) as exc:
        diagnostics.append(
            PlanDiagnostic(
                code="engine_unavailable",
                severity="error",
                message=str(exc),
                field="synthesis.engine",
            )
        )
    if adapter is not None:
        compatibility_check = getattr(adapter, "compatible_api", None)
        if compatibility_check is not None:
            try:
                adapter_api_compatible = bool(compatibility_check())
            except (
                ImportError,
                SyntaxError,
                OSError,
                RuntimeError,
                AttributeError,
                TypeError,
                ValueError,
            ):
                adapter_api_compatible = False
        if not adapter_api_compatible:
            package = getattr(adapter, "package_name", engine_id)
            version = adapter.version()
            diagnostics.append(
                PlanDiagnostic(
                    code="engine_api_incompatible",
                    severity="error",
                    message=(
                        f"{package} {version or '(not installed)'} does not expose the "
                        "request API required by this Readio engine adapter."
                    ),
                    field="synthesis.engine",
                )
            )

    ssmd_provider = (
        adapter.capabilities().voice_binding_namespace or cfg.ssmd.voice_provider
        if adapter is not None
        else cfg.ssmd.voice_provider
    )
    if adapter is not None and adapter_api_compatible:
        resolve_defaults = getattr(adapter, "resolve_defaults", None)
        if resolve_defaults is not None:
            try:
                candidate, default_diags = resolve_defaults(candidate)
                diagnostics.extend(default_diags)
                decisions = list(candidate.decisions)
            except (ImportError, AttributeError, TypeError, ValueError) as exc:
                diagnostics.append(
                    PlanDiagnostic(
                        code="engine_resolution_failed",
                        severity="error",
                        message=f"{engine_id} default resolution failed: {exc}",
                        field="synthesis.engine",
                    )
                )

        options: dict[str, Any] = {
            "rate": candidate.speed,
            "speed": candidate.speed,
            "pause_mode": candidate.pause_mode,
            "short_sentence": candidate.short_sentence,
            "allow_experimental": candidate.allow_experimental,
            "ssmd_voice_bindings": dict(request.voice_bindings),
        }
        optional_options = {
            "model_source": candidate.source,
            "quality": candidate.quality,
            "lexicons": candidate.lexicons,
            "g2p_fallback": candidate.g2p_fallback,
            "lexicon_data_policy": candidate.lexicon_data_policy,
            "language_detection": candidate.language_detection,
            "detect_languages": candidate.detect_languages,
        }
        options.update({key: value for key, value in optional_options.items() if value is not None})
        engine_options = dict(request.synthesis.engine_options)
        voice_file = request.synthesis.voice_file
        if voice_file is not None:
            if engine_id != "pocket":
                diagnostics.append(
                    PlanDiagnostic(
                        code="voice_source_unsupported",
                        severity="error",
                        message="--voice-file is supported only by the pocket engine.",
                        field="synthesis.voice_file",
                    )
                )
            else:
                try:
                    voice_path = Path(voice_file).expanduser().resolve(strict=True)
                    if not voice_path.is_file():
                        raise ValueError(f"reference voice is not a regular file: {voice_path}")
                    voice_source = {
                        "kind": "reference",
                        "value": str(voice_path),
                        "sha256": hashlib.sha256(voice_path.read_bytes()).hexdigest(),
                    }
                except (OSError, ValueError) as exc:
                    diagnostics.append(
                        PlanDiagnostic(
                            code="voice_source_invalid",
                            severity="error",
                            message=str(exc),
                            field="synthesis.voice_file",
                        )
                    )
                else:
                    engine_options["voice_source"] = voice_source
        unsupported_options = sorted(set(engine_options) - adapter.capabilities().option_names)
        for name in unsupported_options:
            diagnostics.append(
                PlanDiagnostic(
                    code="engine_option_unsupported",
                    severity="error",
                    message=f"Engine {engine_id!r} does not support option {name!r}.",
                    field=f"synthesis.engine_options.{name}",
                )
            )
        engine_request = EngineRequest(
            engine=engine_id,
            target_id=candidate.model,
            language=candidate.language,
            voice=candidate.voice,
            speaker=request.synthesis.speaker,
            options=options,
            offline=request.synthesis.offline,
            refresh=request.synthesis.refresh,
            engine_options=engine_options,
        )
        try:
            selection, engine_diags = adapter.resolve(engine_request)
            diagnostics.extend(engine_diags)
            validate_selection = getattr(adapter, "validate_selection", None)
            if validate_selection is not None:
                try:
                    diagnostics.extend(validate_selection(selection))
                    target_metadata = getattr(adapter, "target_metadata", None)
                    if target_metadata is not None:
                        metadata = target_metadata(selection)
                        if metadata:
                            selection = replace(selection, metadata=dict(metadata))
                except (ImportError, AttributeError, TypeError, ValueError) as exc:
                    diagnostics.append(
                        PlanDiagnostic(
                            code="engine_validation_failed",
                            severity="error",
                            message=f"{engine_id} selection validation failed: {exc}",
                            field="synthesis.engine",
                        )
                    )
        except (ImportError, AttributeError, TypeError, ValueError) as exc:
            diagnostics.append(
                PlanDiagnostic(
                    code="engine_resolution_failed",
                    severity="error",
                    message=f"{engine_id} selection failed: {exc}",
                    field="synthesis.engine",
                )
            )

    output_plan, output_diags, output_decisions = _plan_output(
        request.output, cfg, input_plan=input_plan
    )
    diagnostics.extend(output_diags)
    decisions.extend(output_decisions)

    policy = PlanningPolicy(
        language=candidate.language,
        unit=candidate.unit,
        text_preparation="spokenform",
        document_format="ssmd" if effective_doc.format == "ssmd" else "plain",
        pause_mode=candidate.pause_mode,
        spacy_policy=candidate.spacy,
        language_detection=candidate.language_detection,
        detect_languages=tuple(candidate.detect_languages or ()),
    )
    planning = PlanningPlanV2(
        language=candidate.language,
        unit=candidate.unit,
        text_preparation=policy.text_preparation,
        pause_mode=candidate.pause_mode,
        spacy=candidate.spacy,
        language_detection=candidate.language_detection,
        detect_languages=candidate.detect_languages,
    )
    semantic_plan_ref = SemanticPlanRef()
    semantic: CompiledSemanticPlan | None = None
    render: RenderPlanV2 | None = None
    try:
        semantic = compile_semantic_plan(effective_doc, planning=policy)
        semantic_plan_ref = SemanticPlanRef(
            format="utterplan",
            schema_version=semantic.plan.schema_version,
            plan_id=semantic.plan_id,
            sha256=semantic.sha256,
            path=None,
        )
    except (ImportError, AttributeError, TypeError, ValueError, RenderError) as exc:
        diagnostics.append(
            PlanDiagnostic(
                code="semantic_plan_failed",
                severity="error",
                message=f"semantic plan compilation failed: {exc}",
                field="planning",
                source_path=effective_doc.source_path,
            )
        )
    if adapter is not None and selection is not None and semantic is not None:
        selected_voice_source = selection.metadata.get("voice_source")
        if (
            isinstance(selected_voice_source, Mapping)
            and selected_voice_source.get("kind") == "reference"
            and isinstance(selected_voice_source.get("value"), str)
            and isinstance(selected_voice_source.get("sha256"), str)
        ):
            voice_target = VoiceSourceV2(
                kind="reference",
                value=selected_voice_source["value"],
                sha256=selected_voice_source["sha256"],
            )
        elif selection.voice is not None:
            voice_target = VoiceSourceV2(kind="named", value=selection.voice)
        else:
            voice_target = None
        target = RenderTargetV2(
            id=selection.target_id,
            language=selection.language,
            voice=voice_target,
            speaker=selection.speaker,
            options=dict(selection.options),
            metadata=dict(selection.metadata),
        )
        render = RenderPlanV2(
            engine=selection.engine,
            default_target=target,
            rate=candidate.speed,
            options=dict(selection.options),
        )
        render = replace(render, render_id=render_identity(semantic.sha256, render))

    environment_packages = {
        "readio": _package_version("readio"),
        "utterplan": _package_version("utterplan"),
        "ssmd": _package_version("ssmd"),
        "audiocompose": _package_version("audiocompose"),
        "onnxvoice": _package_version("onnxvoice"),
    }
    if adapter is not None:
        engine_version = adapter.version()
        if engine_version is not None:
            package_name = getattr(adapter, "package_name", engine_id)
            environment_packages[package_name] = engine_version
    environment = EnvironmentPlanV2(
        packages=environment_packages,
        ffmpeg_available=ffmpeg_executable() is not None,
    )
    ssmd_bindings: tuple[VoiceBindingPlan, ...] = ()
    if (
        effective_doc.format == "ssmd"
        and semantic is not None
        and selection is not None
        and render is not None
        and render.default_target is not None
    ):
        role_bindings, ssmd_bindings, _ssmd_unresolved, role_diagnostics = _resolve_v2_ssmd_roles(
            semantic,
            cfg,
            request,
            ssmd_provider,
            adapter,
            selection,
            render.default_target,
            effective_doc.source_path,
        )
        diagnostics.extend(role_diagnostics)
        for binding in ssmd_bindings:
            decisions.append(
                ResolutionDecision(
                    field=f"ssmd.bindings.{binding.reference}",
                    value=binding.voice,
                    origin=binding.origin,
                    locator=binding.locator,
                )
            )
        render = replace(
            render,
            role_bindings=role_bindings,
            render_id=None,
        )
        render = replace(
            render,
            render_id=render_identity(semantic.sha256, render),
        )
    has_errors = any(d.severity == "error" for d in diagnostics)
    plan = ReadioPlanV2(
        schema="readio.plan.v2",
        ok=not has_errors and semantic is not None and selection is not None,
        operation=request.operation,
        input=input_plan,
        planning=planning,
        semantic_plan=semantic_plan_ref,
        render=render,
        output=output_plan,
        composition=CompositionPlanV2(
            target_lufs=request.composition.target_lufs,
            true_peak_ceiling_dbtp=request.composition.true_peak_ceiling_dbtp,
            peak_policy=request.composition.peak_policy,
            clip_policy=request.composition.clip_policy,
            sample_rate=request.composition.sample_rate,
        ),
        environment=environment,
        decisions=tuple(decisions),
        diagnostics=tuple(diagnostics),
    )
    return ResolvedExecutionV2(
        plan=plan,
        semantic=semantic,
        document=effective_doc,
        selection=selection,
    )


def resolve_plan_v2(cfg: ReadioConfig, request: PlanRequest) -> ReadioPlanV2:
    """Resolve the serializable engine-neutral v2 plan."""
    return resolve_execution_v2(cfg, request).plan


# ---------------------------------------------------------------------------
# Plan formatting (human-readable)
# ---------------------------------------------------------------------------


def format_plan_human(plan: ReadioPlanV2) -> str:
    """Format an engine-neutral v2 plan for terminal output."""
    return format_plan_v2_human(plan)


# =========================================================================
# readio.plan.v2 — engine-neutral schema
# =========================================================================


def render_identity(semantic_sha256: str, render: RenderPlanV2) -> str:
    """Return an acoustic identity excluding provenance and target metadata."""

    def acoustic_target(target: RenderTargetV2 | None) -> dict[str, Any] | None:
        if target is None:
            return None
        result: dict[str, Any] = {"id": target.id, "language": target.language}
        if target.voice is not None:
            voice = target.voice.to_dict()
            if target.voice.kind == "reference":
                voice = {"kind": "reference", "sha256": target.voice.sha256}
            result["voice"] = voice
        if target.speaker is not None:
            result["speaker"] = target.speaker
        if target.options:
            result["options"] = dict(target.options)
        return result

    payload = {
        "semantic_plan_sha256": semantic_sha256,
        "engine": render.engine,
        "default_target": acoustic_target(render.default_target),
        "role_bindings": [
            {"role": binding.role, "target": acoustic_target(binding.target)}
            for binding in render.role_bindings
        ],
        "rate": render.rate,
        "options": dict(render.options),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True, slots=True)
class SemanticPlanRef:
    """Reference to a persisted UtterancePlan."""

    format: str = "utterplan"
    schema_version: int = CURRENT_SCHEMA_VERSION
    plan_id: str = ""
    sha256: str = ""
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "sha256": self.sha256,
            "path": self.path,
        }


@dataclass(frozen=True, slots=True)
class VoiceSourceV2:
    """Explicit named or reference-audio voice identity."""

    kind: Literal["named", "reference"]
    value: str
    sha256: str | None = None

    def __post_init__(self) -> None:
        if self.kind == "reference" and not self.sha256:
            raise ValueError("reference voice sources require a stable SHA-256")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": self.kind, "value": self.value}
        if self.sha256 is not None:
            result["sha256"] = self.sha256
        return result


@dataclass(frozen=True, slots=True)
class RenderTargetV2:
    """Engine-neutral concrete target for a plan-v2 render."""

    id: str
    language: str
    voice: VoiceSourceV2 | None = None
    speaker: str | int | None = None
    options: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "language": self.language}
        if self.voice is not None:
            result["voice"] = self.voice.to_dict()
        if self.speaker is not None:
            result["speaker"] = self.speaker
        if self.options:
            result["options"] = dict(self.options)
        if self.metadata:
            result["metadata"] = dict(self.metadata)
        return result


@dataclass(frozen=True, slots=True)
class RoleTargetBindingV2:
    """Map one semantic SSMD role to a concrete render target."""

    role: str
    target: RenderTargetV2
    origin: str
    locator: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "role": self.role,
            "target": self.target.to_dict(),
            "origin": self.origin,
        }
        if self.locator is not None:
            result["locator"] = self.locator
        return result


@dataclass(frozen=True, slots=True)
class RenderPlanV2:
    """Engine-neutral render targets and execution options."""

    engine: str
    default_target: RenderTargetV2 | None = None
    role_bindings: tuple[RoleTargetBindingV2, ...] = ()
    rate: float = 1.0
    options: Mapping[str, Any] = field(default_factory=dict)
    render_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "default_target": (
                self.default_target.to_dict() if self.default_target is not None else None
            ),
            "role_bindings": [binding.to_dict() for binding in self.role_bindings],
            "rate": self.rate,
            "options": dict(self.options),
            "render_id": self.render_id,
        }


@dataclass(frozen=True, slots=True)
class EnvironmentPlanV2:
    """Engine-neutral environment info for plan v2."""

    packages: dict[str, str] = field(default_factory=dict)
    ffmpeg_available: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "packages": dict(self.packages),
            "ffmpeg_available": self.ffmpeg_available,
        }


@dataclass(frozen=True, slots=True)
class PlanningPlanV2:
    """Planning configuration for plan v2."""

    language: str
    unit: str
    text_preparation: str | None = None
    pause_mode: str = "auto"
    spacy: str | None = None
    language_detection: str | None = None
    detect_languages: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "language": self.language,
            "unit": self.unit,
            "pause_mode": self.pause_mode,
        }
        if self.text_preparation is not None:
            d["text_preparation"] = self.text_preparation
        if self.spacy is not None:
            d["spacy"] = self.spacy
        if self.language_detection is not None:
            d["language_detection"] = self.language_detection
        if self.detect_languages is not None:
            d["detect_languages"] = list(self.detect_languages)
        return d


@dataclass(frozen=True, slots=True)
class CompositionPlanV2:
    """Resolved AudioCompose output policy for a bounded render."""

    target_lufs: float | None = None
    true_peak_ceiling_dbtp: float = -1.0
    peak_policy: Literal["reduce_gain", "error"] = "reduce_gain"
    clip_policy: Literal["clamp", "warn", "error"] = "clamp"
    sample_rate: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_lufs": self.target_lufs,
            "true_peak_ceiling_dbtp": self.true_peak_ceiling_dbtp,
            "peak_policy": self.peak_policy,
            "clip_policy": self.clip_policy,
            "sample_rate": self.sample_rate,
        }


@dataclass(frozen=True, slots=True)
class ReadioPlanV2:
    """Engine-neutral top-level plan for Readio."""

    schema: str = "readio.plan.v2"
    ok: bool = True
    operation: Literal["speak", "render"] = "render"
    input: InputPlan = field(
        default_factory=lambda: InputPlan(
            source_path=None,
            source_kind="text",
            requested_format="text",
            format="text",
            source_sha256="",
            selector="all",
        )
    )
    planning: PlanningPlanV2 = field(
        default_factory=lambda: PlanningPlanV2(language="en-us", unit="paragraph")
    )
    semantic_plan: SemanticPlanRef = field(default_factory=SemanticPlanRef)
    render: RenderPlanV2 | None = None
    output: OutputPlan = field(
        default_factory=lambda: OutputPlan(
            mode="file",
            format=None,
            encoder_backend=None,
            path=None,
            path_origin="none",
            force=False,
        )
    )
    composition: CompositionPlanV2 = field(default_factory=CompositionPlanV2)
    environment: EnvironmentPlanV2 = field(default_factory=EnvironmentPlanV2)
    decisions: tuple[ResolutionDecision, ...] = ()
    diagnostics: tuple[PlanDiagnostic, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "ok": self.ok,
            "operation": self.operation,
            "input": self.input.to_dict(),
            "planning": self.planning.to_dict(),
            "semantic_plan": self.semantic_plan.to_dict(),
            "render": self.render.to_dict() if self.render is not None else None,
            "output": self.output.to_dict(),
            "composition": self.composition.to_dict(),
            "environment": self.environment.to_dict(),
            "decisions": [d.to_dict() for d in self.decisions],
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }

    def to_json_dict(self) -> dict[str, Any]:
        return self.to_dict()


def format_plan_v2_human(plan: ReadioPlanV2) -> str:
    """Format an engine-neutral v2 plan for terminal output."""
    lines = [
        "Input",
        f"  Source:    {plan.input.source_path or '(stdin)'}",
        f"  Format:    {plan.input.format}",
        f"  SHA256:    {plan.input.source_sha256[:16]}...",
        "",
        "Planning",
        f"  Language:  {plan.planning.language}",
        f"  Unit:      {plan.planning.unit}",
        f"  Pause:     {plan.planning.pause_mode}",
        "",
        "Semantic plan",
        f"  Plan ID:   {plan.semantic_plan.plan_id or '(none)'}",
        f"  SHA256:    {plan.semantic_plan.sha256 or '(none)'}",
    ]
    if plan.render is not None:
        target = plan.render.default_target
        voice = target.voice.value if target is not None and target.voice is not None else "(none)"
        lines.extend(
            [
                "",
                "Render",
                f"  Engine:    {plan.render.engine}",
                f"  Target:    {target.id if target is not None else '(role targets only)'}",
                f"  Voice:     {voice}",
                f"  Role count: {len(plan.render.role_bindings)}",
                f"  Render ID: {plan.render.render_id or '(none)'}",
            ]
        )
    if plan.output.format is not None:
        lines.extend(["", "Output", f"  Format:    {plan.output.format}"])
    if plan.diagnostics:
        lines.extend(["", "Diagnostics"])
        lines.extend(f"  [{d.code}] {d.message}" for d in plan.diagnostics)
    lines.append("")
    lines.append("Plan is executable." if plan.ok else "Plan has errors.")
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
    "CompositionOptions",
    "CompositionPlanV2",
    "EnvironmentPlan",
    "EnvironmentPlanV2",
    "InputPlan",
    "InputRequest",
    "LanguageProfilePlan",
    "ModelPlan",
    "OutputPlan",
    "OutputRequest",
    "PlanDiagnostic",
    "PlanRequest",
    "PlanningPlanV2",
    "ReadioPlan",
    "ReadioPlanV2",
    "RenderPlanV2",
    "RenderTargetV2",
    "ResolutionDecision",
    "RoleTargetBindingV2",
    "SSMDPlan",
    "SemanticPlanRef",
    "SynthesisPlan",
    "SynthesisRequest",
    "VoiceBindingPlan",
    "VoiceSourceV2",
    "format_plan_human",
    "resolve_plan",
    "resolve_plan_v2",
]
