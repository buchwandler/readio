from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ssmd as ssmd_api

from .config import ReadioConfig
from .errors import SSMDInputError, VoiceResolutionError
from .synthesis import ResolvedSynthesis


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
    for index, annotation in annotations:
        reference = annotation.attrs.get("voice")
        if not isinstance(reference, str) or not reference:
            continue
        source_start = annotation.source_start
        order = source_start if source_start is not None else len(text) + index
        line = text.count("\n", 0, source_start) + 1 if source_start is not None else None
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

    @property
    def resolved(self) -> bool:
        return self.voice is not None


def resolve_voice_references(
    text: str,
    cfg: ReadioConfig,
    *,
    available_voices: tuple[str, ...] | None = None,
    additional_bindings: Mapping[str, str] | None = None,
    project_bindings: Mapping[str, str] | None = None,
    provider: str | None = None,
    source_path: Path | None = None,
    parsed: ParsedSSMD09 | None = None,
) -> tuple[ResolvedVoiceReference, ...]:
    """Resolve every SSMD voice reference with origin/locator tracking.

    This is the centralized resolution primitive reused by preflight,
    plan, and render config creation.  Raises :class:`SSMDInputError`
    when the document cannot be parsed.

    Precedence:
        document-local binding > invocation binding > project binding > configured role > direct voice
    """
    provider = provider or cfg.ssmd.voice_provider

    settings = cfg.voices.get(provider)

    parsed = parsed or parse_ssmd_09(text, source_path=source_path)
    references = parsed.voice_references
    # Gather binding sources
    document = document_voice_bindings(text, source_path=source_path, parsed=parsed).get(
        provider, {}
    )
    runtime = dict(additional_bindings or {})
    project = dict(project_bindings or {})
    configured_roles = settings.roles if settings is not None else {}
    if available_voices is None:
        available_voices = tuple(settings.ids) if settings is not None else ()

    results: list[ResolvedVoiceReference] = []

    for use in references:
        ref = use.reference
        line = use.lines[0] if use.lines else None

        # Document-local binding
        if ref in document:
            target = document[ref]
            diag = None
            if target not in available_voices:
                diag = Diagnostic(
                    code="ssmd.voice_unavailable",
                    severity="error",
                    message=f"Document binding {ref!r} -> {target!r} is not available "
                    f"for the active model",
                    line=line,
                )
            results.append(
                ResolvedVoiceReference(
                    reference=ref,
                    voice=target,
                    origin="document",
                    locator="ssmd.front_matter",
                    diagnostic=diag,
                )
            )
            continue

        # Invocation binding
        if ref in runtime:
            target = runtime[ref]
            diag = None
            if target not in available_voices:
                diag = Diagnostic(
                    code="ssmd.voice_unavailable",
                    severity="error",
                    message=f"Invocation binding {ref!r} -> {target!r} is not available "
                    f"for the active model",
                    line=line,
                )
            results.append(
                ResolvedVoiceReference(
                    reference=ref,
                    voice=target,
                    origin="cli",
                    locator="request.voice_bindings",
                    diagnostic=diag,
                )
            )
            continue

        # Project binding
        if ref in project:
            target = project[ref]
            diagnostic = None
            if target not in available_voices:
                diagnostic = Diagnostic(
                    code="ssmd.voice_unavailable",
                    severity="error",
                    message=f"Project binding {ref!r} -> {target!r} is not available "
                    f"for the active model",
                    line=line,
                )
            results.append(
                ResolvedVoiceReference(
                    reference=ref,
                    voice=target,
                    origin="project",
                    locator=f"project.settings.ssmd.voice_bindings.{provider}.{ref}",
                    diagnostic=diagnostic,
                )
            )
            continue

        # Configured role
        if ref in configured_roles:
            target = configured_roles[ref]
            if target in available_voices:
                results.append(
                    ResolvedVoiceReference(
                        reference=ref,
                        voice=target,
                        origin="config.voice_role",
                        locator=f"voices.{provider}.roles.{ref}",
                    )
                )
            else:
                # Role exists but voice not available for this model
                results.append(
                    ResolvedVoiceReference(
                        reference=ref,
                        voice=None,
                        origin="config.voice_role",
                        locator=f"voices.{provider}.roles.{ref}",
                        diagnostic=Diagnostic(
                            code="ssmd.unresolved_voice",
                            severity="error",
                            message=f"Role {ref!r} is configured but voice {target!r} "
                            f"is not available for the active model",
                            line=line,
                        ),
                    )
                )
            continue

        # Direct voice reference
        if ref in available_voices:
            results.append(
                ResolvedVoiceReference(
                    reference=ref,
                    voice=ref,
                    origin="direct",
                )
            )
            continue

        # Unresolved
        results.append(
            ResolvedVoiceReference(
                reference=ref,
                voice=None,
                origin=None,
                diagnostic=Diagnostic(
                    code="ssmd.unresolved_voice",
                    severity="error",
                    message=f"Cannot resolve SSMD voice reference {ref!r}. "
                    f"Provide --voice-bind {ref}=<voice-id> or configure "
                    f"voices.{provider}.roles.{ref}",
                    line=line,
                ),
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
    provider = cfg.ssmd.voice_provider
    if synthesis is not None and synthesis.resolved_model is not None:
        model = synthesis.resolved_model
        return model.id, model.voices
    if synthesis is not None and synthesis.model is not None and synthesis.model_voices is not None:
        return synthesis.model, synthesis.model_voices
    return None, tuple(cfg.voices[provider].ids)


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
    provider = cfg.ssmd.voice_provider
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
    for role, target in cfg.voices[provider].roles.items():
        if role not in referenced and role not in document and target in available:
            defaults[role] = target
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

    provider = cfg.ssmd.voice_provider
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
            + f"\n\nConfigure voices.{result.provider}.roles.{references[0].reference} or add document-local bindings:\n"
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
