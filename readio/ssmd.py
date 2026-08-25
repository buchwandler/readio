from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ssmd as ssmd_api

from .config import ReadioConfig
from .errors import SSMDInputError, VoiceResolutionError


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


def document_voice_bindings(text: str) -> dict[str, dict[str, str]]:
    """Read and normalize document-local voice bindings through SSMD's API."""

    try:
        header = ssmd_api.parse_front_matter(text).data
    except Exception as exc:
        raise SSMDInputError(f"invalid SSMD front matter: {exc}") from exc

    raw_bindings = header.get("voice_bindings", {})
    if raw_bindings is None:
        return {}
    if not isinstance(raw_bindings, Mapping):
        raise SSMDInputError("SSMD front matter voice_bindings must be a mapping")

    normalized: dict[str, dict[str, str]] = {}
    for provider, values in raw_bindings.items():
        if not isinstance(provider, str) or not provider:
            raise SSMDInputError("SSMD voice binding provider names must be non-empty strings")
        if not isinstance(values, Mapping):
            raise SSMDInputError(f"SSMD voice_bindings.{provider} must be a mapping")
        provider_bindings: dict[str, str] = {}
        for reference, target in values.items():
            if (
                not isinstance(reference, str)
                or not reference
                or not isinstance(target, str)
                or not target
            ):
                raise SSMDInputError(
                    f"SSMD voice_bindings.{provider} must map non-empty roles to voice IDs"
                )
            provider_bindings[reference] = target
        normalized[provider] = provider_bindings
    return normalized


def _validated_runtime_bindings(
    cfg: ReadioConfig,
    additional_bindings: Mapping[str, str] | None,
) -> dict[str, str]:
    if additional_bindings is None:
        return {}
    provider = cfg.ssmd.voice_provider
    available = cfg.voices[provider].ids
    bindings = dict(additional_bindings)
    for reference, target in bindings.items():
        if not isinstance(reference, str) or not reference:
            raise ValueError("voice binding roles must be non-empty strings")
        if not isinstance(target, str) or not target:
            raise ValueError("voice binding targets must be non-empty strings")
        if target not in available:
            choices = ", ".join(available)
            raise ValueError(
                f"voice {target!r} is not configured for provider {provider!r}; "
                f"available voices: {choices}"
            )
    return bindings


def default_role_bindings(
    text: str,
    cfg: ReadioConfig,
    additional_bindings: Mapping[str, str] | None = None,
) -> dict[str, dict[str, str]]:
    provider = cfg.ssmd.voice_provider
    document = document_voice_bindings(text).get(provider, {})
    configured = cfg.voices[provider].roles
    defaults = {role: target for role, target in configured.items() if role not in document}
    runtime = _validated_runtime_bindings(cfg, additional_bindings)
    defaults.update(
        {
            role: target
            for role, target in runtime.items()
            if role not in document and role not in configured
        }
    )
    return {provider: defaults} if defaults else {}


def build_ssmd_render_config(
    text: str,
    cfg: ReadioConfig,
    additional_bindings: Mapping[str, str] | None = None,
) -> Any:
    from pykokoro import SSMDRenderConfig

    return SSMDRenderConfig(
        provider=cfg.ssmd.voice_provider,
        voice_bindings=default_role_bindings(text, cfg, additional_bindings),
        missing_voice="error",
    )


def _resolved_target(
    reference: str,
    document: Mapping[str, str],
    runtime: Mapping[str, str],
    cfg: ReadioConfig,
) -> str | None:
    settings = cfg.voices[cfg.ssmd.voice_provider]
    if reference in document:
        return document[reference]
    if reference in settings.roles:
        return settings.roles[reference]
    if reference in settings.ids:
        return reference
    return runtime.get(reference)


def analyze_ssmd(
    text: str,
    cfg: ReadioConfig,
    *,
    source_path: Path | None = None,
    additional_bindings: Mapping[str, str] | None = None,
) -> SSMDPreflightResult:
    """Analyze an SSMD document without raising for unresolved voice references."""

    provider = cfg.ssmd.voice_provider
    runtime = _validated_runtime_bindings(cfg, additional_bindings)
    try:
        ssmd_api.parse_ssmd(text, strict_parse=True)
        references = ssmd_api.extract_voice_references(text)
    except Exception as exc:
        raise SSMDInputError(f"SSMD consumer parse failed: {exc}", source_path=source_path) from exc

    document = document_voice_bindings(text).get(provider, {})
    defaults = default_role_bindings(text, cfg).get(provider, {})
    unresolved: list[Any] = []
    diagnostics: list[Diagnostic] = []
    available = cfg.voices[provider].ids

    for use in references:
        if use.reference in document:
            target = document[use.reference]
            if target not in available:
                diagnostics.append(
                    Diagnostic(
                        code="ssmd.document_binding_invalid",
                        severity="error",
                        message=(
                            f"document binding {use.reference!r} -> {target!r} is not a configured "
                            f"voice for provider {provider!r}"
                        ),
                        line=use.lines[0] if use.lines else None,
                    )
                )
            continue
        target = _resolved_target(use.reference, document, runtime, cfg)
        if target is None or target not in available:
            unresolved.append(use)

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
) -> VoiceResolutionError:
    available = tuple(cfg.voices[result.provider].ids)
    invalid = next(
        (item for item in result.diagnostics if item.code == "ssmd.document_binding_invalid"),
        None,
    )
    if invalid is not None:
        message = invalid.message
        reference = result.document_bindings.get(result.voice_references[0].reference, "")
        first_reference = next(
            use.reference
            for use in result.voice_references
            if any(
                diagnostic.line in use.lines
                for diagnostic in result.diagnostics
                if diagnostic.code == "ssmd.document_binding_invalid"
            )
        )
        reference = first_reference
    else:
        references = result.unresolved_voice_references
        message = (
            f"cannot resolve {len(references)} SSMD voice reference"
            f"{'s' if len(references) != 1 else ''} for provider {result.provider!r}\n"
            + "\n".join(
                f"  {use.reference} ({use.count} uses)" for use in references
            )
            + "\n\nConfigured voices: "
            + ", ".join(available)
            + "\n\n"
            + f"\n\nConfigure voices.{result.provider}.roles.{references[0].reference} or add document-local bindings:\n"
            + "  voice_bindings:\n"
            + f"    {result.provider}:\n"
            + "".join(f"      {use.reference}: <voice-id>\n" for use in references)
            + "\nOr save reusable Readio roles with:\n"
            + "\n".join(
                f"  readio voices bind {use.reference} <voice-id>" for use in references
            )
            + "\n\nOr resolve this invocation with:\n"
            + "  readio render --file FILE \\\n"
            + "".join(f"    --voice-bind {use.reference}=<voice-id> \\\n" for use in references).rstrip(" \\\n")
            + f"\n\nRun `readio voices list --provider {result.provider}` to inspect valid voice IDs."
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
) -> SSMDPreflightResult:
    """Check an SSMD document with the same binding map used by the pipeline."""

    result = analyze_ssmd(
        text,
        cfg,
        source_path=source_path,
        additional_bindings=additional_bindings,
    )
    if not result.ok:
        raise _voice_resolution_error(result, cfg, source_path=source_path)
    return result
