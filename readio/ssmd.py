from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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
    unresolved_references: tuple[str, ...]
    diagnostics: tuple[Diagnostic, ...]

    @property
    def ok(self) -> bool:
        return not self.unresolved_references and not any(
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


def default_role_bindings(text: str, cfg: ReadioConfig) -> dict[str, dict[str, str]]:
    provider = cfg.ssmd.voice_provider
    document = document_voice_bindings(text).get(provider, {})
    configured = cfg.voices[provider].roles
    defaults = {role: target for role, target in configured.items() if role not in document}
    return {provider: defaults} if defaults else {}


def build_ssmd_render_config(text: str, cfg: ReadioConfig) -> Any:
    from pykokoro import SSMDRenderConfig

    return SSMDRenderConfig(
        provider=cfg.ssmd.voice_provider,
        voice_bindings=default_role_bindings(text, cfg),
        missing_voice="error",
    )


def _resolved_target(reference: str, document: Mapping[str, str], cfg: ReadioConfig) -> str | None:
    settings = cfg.voices[cfg.ssmd.voice_provider]
    if reference in document:
        return document[reference]
    if reference in settings.roles:
        return settings.roles[reference]
    if reference in settings.ids:
        return reference
    return None


def preflight_ssmd(
    text: str,
    cfg: ReadioConfig,
    *,
    source_path: Any = None,
) -> SSMDPreflightResult:
    """Check an SSMD document with the same binding map used by the pipeline."""

    provider = cfg.ssmd.voice_provider
    render_config = build_ssmd_render_config(text, cfg)
    try:
        ssmd_api.parse_ssmd(text, strict_parse=True)
        references = ssmd_api.extract_voice_references(text)
    except Exception as exc:
        raise SSMDInputError(f"SSMD consumer parse failed: {exc}", source_path=source_path) from exc

    document = document_voice_bindings(text).get(provider, {})
    defaults = default_role_bindings(text, cfg).get(provider, {})
    unresolved: list[str] = []
    diagnostics: list[Diagnostic] = []
    for use in references:
        target = _resolved_target(use.reference, document, cfg)
        if target is None or target not in cfg.voices[provider].ids:
            unresolved.append(use.reference)
            message = (
                f"readio: unresolved SSMD voice role {use.reference!r} for provider {provider!r}. "
                f"Configure voices.{provider}.roles.{use.reference}, bind it in the document header, "
                "or use a configured concrete voice ID."
            )
            raise VoiceResolutionError(
                message,
                provider=provider,
                reference=use.reference,
                source_path=source_path,
            )

    if not render_config.voice_bindings and document:
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
        unresolved_references=tuple(unresolved),
        diagnostics=tuple(diagnostics),
    )
