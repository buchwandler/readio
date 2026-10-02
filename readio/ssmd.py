from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ssmd as ssmd_api

from . import config as config_internal
from .config import ReadioConfig
from .engines.registry import normalize_engine_id
from .errors import SSMDInputError, VoiceResolutionError
from .role_targets import (
    VoiceTarget,
    engine_for_ssmd_namespace,
    ssmd_namespace_for_engine,
)
from .synthesis import ResolvedSynthesis
from .voice_refs import public_system_for_engine
from .voices import resolve_voice_reference


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    severity: str
    message: str
    line: int | None = None
    column: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "line": self.line,
            "column": self.column,
        }


@dataclass(frozen=True, slots=True)
class VoiceReferenceUse:
    reference: str
    count: int
    lines: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ParsedSSMD09:
    structure: Any
    voice_references: tuple[VoiceReferenceUse, ...]

    @property
    def header(self) -> Mapping[str, Any]:
        return self.structure.header

    @property
    def annotations(self) -> tuple[Any, ...]:
        return tuple(self.structure.annotations)

    @property
    def events(self) -> tuple[Any, ...]:
        return tuple(self.structure.events)


def parse_ssmd_09(text: str, *, source_path: Path | None = None) -> ParsedSSMD09:
    try:
        structure = ssmd_api.parse_structure(
            text,
            default_lang=None,
            parse_yaml_header=True,
            resolve_defaults=False,
            dialect="0.9",
        )
    except Exception as exc:
        raise SSMDInputError(f"SSMD 0.9 input is required: {exc}", source_path=source_path) from exc

    errors = [item for item in structure.diagnostics if item.severity == "error"]
    if errors:
        diagnostic = errors[0]
        details = {
            "diagnostics": [
                {
                    "code": item.code,
                    "severity": item.severity,
                    "message": item.message,
                    "source_start": item.source_start,
                    "source_end": item.source_end,
                    "line": item.line,
                    "column": item.column,
                }
                for item in errors
            ]
        }
        raise SSMDInputError(
            f"SSMD 0.9 input is required: {diagnostic.message} ({diagnostic.code})",
            source_path=source_path,
            details=details,
        )

    grouped: dict[str, list[tuple[int, int | None]]] = {}
    annotations = sorted(
        enumerate(structure.annotations),
        key=lambda pair: (
            pair[1].source_start if pair[1].source_start is not None else len(text) + pair[0],
            pair[0],
        ),
    )
    source_lines: dict[str, list[int]] = {}
    for match in re.finditer(r"\bvoice\s*=\s*([\"'])(.*?)\1", text):
        reference = match.group(2)
        line = text.count("\n", 0, match.start()) + 1
        source_lines.setdefault(reference, []).append(line)
    source_line_index: dict[str, int] = {}
    for index, annotation in annotations:
        reference = annotation.attrs.get("voice")
        if not isinstance(reference, str) or not reference:
            continue
        source_start = annotation.source_start
        order = source_start if source_start is not None else len(text) + index
        line_index = source_line_index.get(reference, 0)
        lines = source_lines.get(reference, ())
        line = (
            lines[line_index]
            if line_index < len(lines)
            else (text.count("\n", 0, source_start) + 1 if source_start is not None else None)
        )
        source_line_index[reference] = line_index + 1
        grouped.setdefault(reference, []).append((order, line))

    references = tuple(
        VoiceReferenceUse(
            reference=reference,
            count=len(uses),
            lines=tuple(line for _, line in uses if line is not None),
        )
        for reference, uses in grouped.items()
    )
    return ParsedSSMD09(
        structure=structure,
        voice_references=references,
    )


@dataclass(frozen=True, slots=True)
class ResolvedVoiceReference:
    """A single voice reference resolved against available bindings."""

    reference: str
    voice: str | None
    origin: str | None
    locator: str | None = None
    diagnostic: Diagnostic | None = None
    target: VoiceTarget | None = None

    @property
    def resolved(self) -> bool:
        return self.target is not None or self.voice is not None


def _resolve_voice_target(
    value: str | VoiceTarget,
    *,
    engine_hint: str | None,
    offline: bool = False,
    refresh: bool = False,
) -> VoiceTarget | None:
    if isinstance(value, VoiceTarget):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    resolved = resolve_voice_reference(
        value,
        language=None,
        model=None,
        source=None,
        offline=offline,
        refresh=refresh,
        engine=engine_hint,
    )
    if resolved is None:
        if engine_hint is None:
            return None
        try:
            public_system_for_engine(engine_hint)
        except ValueError:
            return VoiceTarget(normalize_engine_id(engine_hint), value)
        return None
    target_engine = resolved.engine or engine_hint
    if target_engine is None:
        return None
    return VoiceTarget(
        normalize_engine_id(target_engine),
        resolved.voice,
        target_id=resolved.target_id,
    )


def resolve_voice_references(
    text: str,
    cfg: ReadioConfig,
    *,
    available_voices: tuple[str, ...] | None = None,
    additional_bindings: Mapping[str, str | VoiceTarget] | None = None,
    project_targets: Mapping[str, VoiceTarget] | None = None,
    configured_targets: Mapping[str, VoiceTarget] | None = None,
    provider: str | None = None,
    engine: str | None = None,
    offline: bool = False,
    refresh: bool = False,
    source_path: Path | None = None,
    parsed: ParsedSSMD09 | None = None,
) -> tuple[ResolvedVoiceReference, ...]:
    """Resolve role references to engine-qualified targets by binding precedence."""
    parsed = parsed or parse_ssmd_09(text, source_path=source_path)
    references = parsed.voice_references
    provider_hint = provider or ssmd_namespace_for_engine(cfg.reader.engine)
    try:
        engine_hint = (
            normalize_engine_id(engine)
            if engine is not None
            else engine_for_ssmd_namespace(provider_hint)
        )
    except ValueError:
        engine_hint = normalize_engine_id(engine or cfg.reader.engine)
    documents = document_voice_bindings(text, source_path=source_path, parsed=parsed)
    invocation = dict(additional_bindings or {})
    project_targets = dict(project_targets or {})
    configured_targets = dict(configured_targets or cfg.roles)
    inventory_voices = set(
        available_voices
        if available_voices is not None
        else tuple(
            sorted(
                {target.voice for target in config_internal.role_targets(cfg, engine_hint).values()}
            )
        )
    )
    results: list[ResolvedVoiceReference] = []
    for use in references:
        reference = use.reference
        line = use.lines[0] if use.lines else None
        target: VoiceTarget | None = None
        origin: str | None = None
        locator: str | None = None
        diagnostic: Diagnostic | None = None

        document_candidates = [
            (namespace, bindings[reference])
            for namespace, bindings in documents.items()
            if reference in bindings
        ]
        if len(document_candidates) > 1:
            names = [namespace for namespace, _voice in document_candidates]
            diagnostic = Diagnostic(
                code="ssmd.voice_binding_ambiguous_engine",
                severity="error",
                message=(
                    f"Document role {reference!r} is bound in multiple provider namespaces: "
                    f"{', '.join(names)}."
                ),
                line=line,
            )
            origin = "document"
            locator = "utterplan.document_metadata.voice_bindings"
        elif document_candidates:
            namespace, bound_voice = document_candidates[0]
            origin = "document"
            locator = "utterplan.document_metadata.voice_bindings"
            try:
                target = VoiceTarget(engine_for_ssmd_namespace(namespace), bound_voice)
            except ValueError as error:
                diagnostic = Diagnostic(
                    code="ssmd.voice_binding_unsupported_provider",
                    severity="error",
                    message=str(error),
                    line=line,
                )
        elif reference in invocation:
            origin = "cli"
            locator = "request.voice_bindings"
            try:
                target = _resolve_voice_target(
                    invocation[reference],
                    engine_hint=engine_hint,
                    offline=offline,
                    refresh=refresh,
                )
            except (TypeError, ValueError) as error:
                diagnostic = Diagnostic(
                    code="ssmd.voice_binding_invalid",
                    severity="error",
                    message=f"Invocation binding {reference!r} could not be resolved: {error}",
                    line=line,
                )
        elif reference in project_targets:
            target = project_targets[reference]
            origin = "project"
            locator = f"project.settings.ssmd.role_bindings.{reference}"
        elif reference in configured_targets:
            target = configured_targets[reference]
            origin = "config.voice_role"
            locator = f"roles.{reference}"
        elif reference in inventory_voices:
            target = VoiceTarget(engine_hint, reference)
            origin = "direct"
        else:
            diagnostic = Diagnostic(
                code="ssmd.unresolved_voice",
                severity="error",
                message=(
                    f"Cannot resolve SSMD voice reference {reference!r}. Provide a role binding "
                    "or an engine-qualified target."
                ),
                line=line,
            )

        if diagnostic is None and target is None:
            diagnostic = Diagnostic(
                code="ssmd.voice_engine_required",
                severity="error",
                message=f"Role {reference!r} does not resolve to an engine-qualified voice target.",
                line=line,
            )
        if (
            diagnostic is None
            and target is not None
            and target.engine == engine_hint
            and available_voices is not None
            and target.voice not in available_voices
        ):
            diagnostic = Diagnostic(
                code="ssmd.voice_unavailable",
                severity="error",
                message=(
                    f"Voice target {reference!r} -> {target.voice!r} is not available "
                    f"for engine {engine_hint!r}."
                ),
                line=line,
            )

        resolved_target = target if diagnostic is None else None
        results.append(
            ResolvedVoiceReference(
                reference=reference,
                voice=target.voice if target is not None else None,
                origin=origin,
                locator=locator,
                diagnostic=diagnostic,
                target=resolved_target,
            )
        )
    return tuple(results)


@dataclass(frozen=True, slots=True)
class SSMDPreflightResult:
    provider: str
    document_bindings: Mapping[str, str]
    default_bindings: Mapping[str, str]
    runtime_bindings: Mapping[str, str]
    voice_references: tuple[Any, ...]
    unresolved_voice_references: tuple[Any, ...]
    unresolved_references: tuple[str, ...]
    diagnostics: tuple[Diagnostic, ...]

    @property
    def ok(self) -> bool:
        return not self.unresolved_voice_references and not any(
            diagnostic.severity == "error" for diagnostic in self.diagnostics
        )


def language_detection_hint(
    text: str,
    *,
    source_path: Path | None = None,
    parsed: ParsedSSMD09 | None = None,
) -> tuple[str, tuple[str, ...]] | None:
    """Return the SSMD language-detection hint from a validated 0.9 parse."""
    parsed = parsed or parse_ssmd_09(text, source_path=source_path)
    header = parsed.header
    raw = header.get("language_detection")
    if raw is None:
        return None
    if isinstance(raw, str):
        mode = raw.strip().lower()
        languages: Any = ()
    elif isinstance(raw, Mapping):
        mode = str(raw.get("mode", "off")).strip().lower()
        languages = raw.get("languages", ())
    else:
        raise SSMDInputError(
            "SSMD front matter language_detection must be a string or mapping",
            source_path=source_path,
        )
    if mode not in {"off", "auto"}:
        raise SSMDInputError(
            "SSMD language_detection.mode must be 'off' or 'auto'",
            source_path=source_path,
        )
    if isinstance(languages, str):
        languages = [languages]
    if not isinstance(languages, (list, tuple)) or any(
        not isinstance(item, str) for item in languages
    ):
        raise SSMDInputError(
            "SSMD language_detection.languages must be a list of strings",
            source_path=source_path,
        )
    normalized = tuple(item.strip().lower().replace("_", "-") for item in languages)
    if any(not item for item in normalized):
        raise SSMDInputError(
            "SSMD language_detection.languages must be non-empty",
            source_path=source_path,
        )
    if len(normalized) != len(set(normalized)):
        raise SSMDInputError(
            "SSMD language_detection.languages must not contain duplicates",
            source_path=source_path,
        )
    return mode, normalized


def document_voice_bindings(
    text: str,
    *,
    source_path: Path | None = None,
    parsed: ParsedSSMD09 | None = None,
) -> dict[str, dict[str, str]]:
    """Read document bindings from a strict 0.9 structural parse."""
    parsed = parsed or parse_ssmd_09(text, source_path=source_path)
    header = parsed.header

    raw_bindings = header.get("voice_bindings", {})
    if raw_bindings is None:
        return {}
    if not isinstance(raw_bindings, Mapping):
        raise SSMDInputError(
            "SSMD front matter voice_bindings must be a mapping",
            source_path=source_path,
        )

    normalized: dict[str, dict[str, str]] = {}
    for provider, values in raw_bindings.items():
        if not isinstance(provider, str) or not provider:
            raise SSMDInputError(
                "SSMD voice binding provider names must be non-empty strings",
                source_path=source_path,
            )
        if not isinstance(values, Mapping):
            raise SSMDInputError(
                f"SSMD voice_bindings.{provider} must be a mapping",
                source_path=source_path,
            )
        provider_bindings: dict[str, str] = {}
        for reference, target in values.items():
            if (
                not isinstance(reference, str)
                or not reference
                or not isinstance(target, str)
                or not target
            ):
                raise SSMDInputError(
                    f"SSMD voice_bindings.{provider} must map non-empty roles to voice IDs",
                    source_path=source_path,
                )
            provider_bindings[reference] = target
        normalized[provider] = provider_bindings
    return normalized


def _available_voice_context(
    cfg: ReadioConfig,
    synthesis: ResolvedSynthesis | None,
) -> tuple[str | None, tuple[str, ...]]:
    if synthesis is not None and synthesis.resolved_model is not None:
        model = synthesis.resolved_model
        return model.id, model.voices
    if synthesis is not None and synthesis.model is not None and synthesis.model_voices is not None:
        return synthesis.model, synthesis.model_voices
    return None, tuple(
        sorted(
            {
                target.voice
                for target in config_internal.role_targets(cfg, cfg.reader.engine).values()
            }
        )
    )


def _validated_runtime_bindings(
    cfg: ReadioConfig,
    additional_bindings: Mapping[str, str] | None,
    synthesis: ResolvedSynthesis | None = None,
) -> dict[str, str]:
    active_model, available = _available_voice_context(cfg, synthesis)
    bindings = dict(additional_bindings or {})
    for reference, target in bindings.items():
        if not isinstance(reference, str) or not reference:
            raise ValueError("voice binding roles must be non-empty strings")
        if not isinstance(target, str) or not target:
            raise ValueError("voice binding targets must be non-empty strings")
        if target not in available:
            choices = ", ".join(available)
            model_label = f" for active model {active_model!r}" if active_model else ""
            raise ValueError(
                f"voice {target!r} is not available{model_label}; available voices: {choices}"
            )
    return bindings


def default_role_bindings(
    text: str,
    cfg: ReadioConfig,
    additional_bindings: Mapping[str, str] | None = None,
    synthesis: ResolvedSynthesis | None = None,
    *,
    source_path: Path | None = None,
    parsed: ParsedSSMD09 | None = None,
) -> dict[str, dict[str, str]]:
    """Effective non-document bindings derived from voice-reference resolution."""
    provider = ssmd_namespace_for_engine(cfg.reader.engine)
    parsed = parsed or parse_ssmd_09(text, source_path=source_path)
    document = document_voice_bindings(text, source_path=source_path, parsed=parsed).get(
        provider, {}
    )
    _, available = _available_voice_context(cfg, synthesis)
    runtime = _validated_runtime_bindings(cfg, additional_bindings, synthesis)
    resolved = resolve_voice_references(
        text,
        cfg,
        available_voices=available,
        additional_bindings=runtime,
        source_path=source_path,
        parsed=parsed,
    )
    defaults = {
        item.reference: item.voice
        for item in resolved
        if item.voice is not None and item.origin in ("config.voice_role", "cli")
    }
    referenced = {item.reference for item in resolved}
    for role, target in config_internal.role_targets(cfg, cfg.reader.engine).items():
        if role not in referenced and role not in document and target.voice in available:
            defaults[role] = target.voice
    for role, target in runtime.items():
        if role not in referenced and role not in document:
            defaults[role] = target
    return {provider: defaults} if defaults else {}


def analyze_ssmd(
    text: str,
    cfg: ReadioConfig,
    *,
    source_path: Path | None = None,
    additional_bindings: Mapping[str, str] | None = None,
    synthesis: ResolvedSynthesis | None = None,
    parsed: ParsedSSMD09 | None = None,
) -> SSMDPreflightResult:
    """Analyze an SSMD document without raising for unresolved voice references."""

    provider = ssmd_namespace_for_engine(cfg.reader.engine)
    parsed = parsed or parse_ssmd_09(text, source_path=source_path)
    runtime = _validated_runtime_bindings(cfg, additional_bindings, synthesis)
    references = parsed.voice_references

    document = document_voice_bindings(text, source_path=source_path, parsed=parsed).get(
        provider, {}
    )
    defaults = default_role_bindings(
        text,
        cfg,
        synthesis=synthesis,
        source_path=source_path,
        parsed=parsed,
    ).get(provider, {})
    diagnostics: list[Diagnostic] = []
    active_model, available = _available_voice_context(cfg, synthesis)

    # The binding decision comes from the shared primitive; this view only
    # translates its results into the historical preflight payload.
    resolved = resolve_voice_references(
        text,
        cfg,
        available_voices=available,
        additional_bindings=runtime,
        source_path=source_path,
        parsed=parsed,
    )
    for item in resolved:
        if (
            item.origin == "document"
            and item.diagnostic is not None
            and item.diagnostic.severity == "error"
        ):
            diagnostics.append(
                Diagnostic(
                    code="ssmd.document_binding_invalid",
                    severity="error",
                    message=(
                        f"document binding {item.reference!r} -> {item.voice!r} is incompatible "
                        f"with active model {active_model!r}"
                        if active_model
                        else f"document binding {item.reference!r} -> {item.voice!r} is not a "
                        f"configured voice for provider {provider!r}"
                    ),
                    line=item.diagnostic.line,
                )
            )
    unresolved = [use for use, item in zip(references, resolved) if item.voice is None]

    if not defaults and document:
        diagnostics.append(
            Diagnostic(
                code="ssmd.document_bindings_only",
                severity="info",
                message="All configured defaults were overridden by document bindings.",
            )
        )
    return SSMDPreflightResult(
        provider=provider,
        document_bindings=dict(document),
        default_bindings=dict(defaults),
        runtime_bindings=dict(runtime),
        voice_references=tuple(references),
        unresolved_voice_references=tuple(unresolved),
        unresolved_references=tuple(use.reference for use in unresolved),
        diagnostics=tuple(diagnostics),
    )


def _header_template(provider: str, references: tuple[Any, ...]) -> dict[str, Any]:
    return {"voice_bindings": {provider: {use.reference: None for use in references}}}


def _voice_resolution_error(
    result: SSMDPreflightResult,
    cfg: ReadioConfig,
    *,
    source_path: Path | None,
    synthesis: ResolvedSynthesis | None = None,
) -> VoiceResolutionError:
    active_model, available = _available_voice_context(cfg, synthesis)
    invalid = next(
        (item for item in result.diagnostics if item.code == "ssmd.document_binding_invalid"),
        None,
    )
    if invalid is not None:
        first_reference = next(
            use.reference
            for use in result.voice_references
            if any(
                diagnostic.line in use.lines
                for diagnostic in result.diagnostics
                if diagnostic.code == "ssmd.document_binding_invalid"
            )
        )
        target = result.document_bindings[first_reference]
        if active_model:
            available_label = ", ".join(available) or "none"
            message = (
                f"Voice {target!r} is not available for active model {active_model!r}. "
                f"Available concrete voices: {available_label}."
            )
        else:
            message = invalid.message
        reference = first_reference
    else:
        references = result.unresolved_voice_references
        message = (
            f"cannot resolve {len(references)} SSMD voice reference"
            f"{'s' if len(references) != 1 else ''} for provider {result.provider!r}\n"
            + "\n".join(f"  {use.reference} ({use.count} uses)" for use in references)
            + (
                f"\n\nAvailable concrete voices for active model {active_model!r}: "
                if active_model
                else "\n\nConfigured voices: "
            )
            + ", ".join(available)
            + "\n\n"
            + f"\n\nConfigure roles.{references[0].reference} as an engine-qualified target or add document-local bindings:\n"
            + "  voice_bindings:\n"
            + f"    {result.provider}:\n"
            + "".join(f"      {use.reference}: <voice-id>\n" for use in references)
            + "\nOr save reusable Readio roles with:\n"
            + "\n".join(f"  readio roles bind {use.reference} <voice-id>" for use in references)
            + "\n\nOr resolve this invocation with:\n"
            + "  readio render --file FILE \\\n"
            + "".join(
                f"    --voice-bind {use.reference}=<voice-id> \\\n" for use in references
            ).rstrip(" \\\n")
            + (
                f"\n\nRun `readio voices list --model {active_model}` to inspect valid voice IDs."
                if active_model
                else f"\n\nRun `readio voices list --provider {result.provider}` to inspect valid voice IDs."
            )
        )
        reference = references[0].reference
    return VoiceResolutionError(
        message,
        provider=result.provider,
        reference=reference,
        references=tuple(result.unresolved_voice_references),
        available_voices=available,
        header_template=_header_template(result.provider, result.unresolved_voice_references),
        source_path=source_path,
    )


def preflight_ssmd(
    text: str,
    cfg: ReadioConfig,
    *,
    source_path: Path | None = None,
    additional_bindings: Mapping[str, str] | None = None,
    synthesis: ResolvedSynthesis | None = None,
    parsed: ParsedSSMD09 | None = None,
) -> SSMDPreflightResult:
    """Check an SSMD document with the same binding map used by the pipeline."""
    result = analyze_ssmd(
        text,
        cfg,
        source_path=source_path,
        additional_bindings=additional_bindings,
        synthesis=synthesis,
        parsed=parsed,
    )
    if not result.ok:
        raise _voice_resolution_error(result, cfg, source_path=source_path, synthesis=synthesis)
    return result
