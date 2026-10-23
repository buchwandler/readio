"""Source-aware inspection and native profile creation for SSMD documents."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import utterplan  # type: ignore[import-not-found]
import voicerender  # type: ignore[import-not-found]

from .profiles import _persist_profile
from .voice_selector import parse_voice_selector


@dataclass(frozen=True, slots=True)
class DocumentVoiceRole:
    """An explicitly declared logical role and its number of SSMD voice blocks."""

    name: str
    blocks: int


@dataclass(frozen=True, slots=True)
class DocumentVoiceRequirements:
    """Voice-role requirements discovered in an SSMD document."""

    source: Path
    roles: tuple[DocumentVoiceRole, ...]
    default_role_candidate: str | None
    uses_default_role: bool = False
    implicit_blocks: int = 0


@dataclass(frozen=True, slots=True)
class RoleVoiceBinding:
    """Readio-owned normalized description of one role's VoiceRender binding."""

    role: str
    engine: str
    target: str
    language: str
    voice: str | None = None
    speaker: str | int | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RoleAlias:
    """A logical role that reuses another role's complete binding values."""

    role: str
    source_role: str


def inspect_document_voice_requirements(
    source: str | Path,
) -> DocumentVoiceRequirements:
    """Inspect explicit SSMD voice roles using UtterPlan's public parser APIs."""

    path = Path(source).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as error:
        raise FileNotFoundError(f"SSMD source does not exist: {path}") from error
    if not resolved.is_file():
        raise ValueError(f"SSMD source is not a file: {resolved}")
    if not resolved.name.casefold().endswith(".ssmd.md"):
        raise ValueError(f"voice-role inspection currently supports .ssmd.md sources: {resolved}")

    text = resolved.read_text(encoding="utf-8")
    config = utterplan.PlannerConfig(
        language="en-US",
        document_format="ssmd",
        text_preparation="ssmd",
        linguistics=utterplan.LinguisticsConfig(use_spacy=False),
        diagnostics=False,
    )
    compiled = utterplan.compile_document(text, input_format="ssmd", config=config)
    inventory = utterplan.logical_role_inventory(compiled.plan)

    ordered_names = list(inventory.explicit_roles)
    if "narrator" in ordered_names:
        ordered_names.remove("narrator")
        ordered_names.insert(0, "narrator")
    roles = tuple(
        DocumentVoiceRole(name=name, blocks=int(inventory.explicit_occurrences[name]))
        for name in ordered_names
    )
    candidate = ordered_names[0] if ordered_names else None
    return DocumentVoiceRequirements(
        source=resolved,
        roles=roles,
        default_role_candidate=candidate,
        uses_default_role=bool(inventory.uses_default_role),
        implicit_blocks=int(inventory.implicit_occurrences),
    )


def _direct_bindings(
    role_names: tuple[str, ...],
    *,
    engine: str,
    target: str,
    language: str,
    voices: Mapping[str, str],
    speakers: Mapping[str, str | int],
    options: Mapping[str, Mapping[str, Any]],
) -> dict[str, RoleVoiceBinding]:
    known = set(role_names)
    supplied_roles = (*voices, *speakers, *options)
    unknown = list(dict.fromkeys(role for role in supplied_roles if role not in known))
    if unknown:
        raise ValueError(f"unknown source role(s): {', '.join(unknown)}")

    bindings: dict[str, RoleVoiceBinding] = {}
    for role in role_names:
        voice = voices.get(role)
        speaker = speakers.get(role)
        role_options = deepcopy(dict(options.get(role, {})))
        if voice is not None and role in speakers:
            raise ValueError(f"role {role!r} cannot use both --voice and --speaker")
        if voice is None and role not in speakers:
            if role_options:
                raise ValueError(
                    f"role {role!r} has --option values but no direct --voice or --speaker binding"
                )
            continue
        if voice is not None:
            selector = parse_voice_selector(voice)
            if selector.voice is None:
                raise ValueError(f"voice selector for role {role!r} does not name a voice")
            bindings[role] = RoleVoiceBinding(
                role=role,
                engine=selector.engine or engine,
                target=selector.target_id or target,
                language=language,
                voice=selector.voice,
                options=role_options,
            )
            continue
        if isinstance(speaker, str) and not speaker.strip():
            raise ValueError(f"speaker for role {role!r} must not be empty")
        bindings[role] = RoleVoiceBinding(
            role=role,
            engine=engine,
            target=target,
            language=language,
            speaker=speaker,
            options=role_options,
        )
    return bindings


def _resolve_role_bindings(
    role_names: tuple[str, ...],
    direct: Mapping[str, RoleVoiceBinding],
    aliases: Mapping[str, str],
) -> dict[str, RoleVoiceBinding]:
    known = set(role_names)
    unknown = list(
        dict.fromkeys(role for role in (*aliases.keys(), *aliases.values()) if role not in known)
    )
    if unknown:
        raise ValueError(f"unknown source role(s) in --same-as: {', '.join(unknown)}")
    for role, source_role in aliases.items():
        if role == source_role:
            raise ValueError(f"role {role!r} cannot be an alias of itself")
        if role in direct:
            raise ValueError(f"role {role!r} has both a direct binding and a --same-as alias")

    resolved = dict(direct)
    resolving: list[str] = []

    def resolve(role: str) -> RoleVoiceBinding:
        if role in resolved:
            return resolved[role]
        if role in resolving:
            cycle = " -> ".join((*resolving, role))
            raise ValueError(f"cyclic role alias: {cycle}")
        source_role = aliases.get(role)
        if source_role is None:
            raise ValueError(f"role {role!r} has no direct voice or speaker binding")
        resolving.append(role)
        try:
            source = resolve(source_role)
        finally:
            resolving.pop()
        binding = RoleVoiceBinding(
            role=role,
            engine=source.engine,
            target=source.target,
            language=source.language,
            voice=source.voice,
            speaker=source.speaker,
            options=deepcopy(source.options or {}),
        )
        resolved[role] = binding
        return binding

    missing = [role for role in role_names if role not in direct and role not in aliases]
    if missing:
        raise ValueError(
            f"missing role bindings: {', '.join(missing)}. Bind each role with "
            "--voice ROLE=VOICE, --speaker ROLE=SPEAKER, or --same-as ROLE=ROLE."
        )
    for role in role_names:
        resolve(role)
    return resolved


def _profile_roles(
    requirements: DocumentVoiceRequirements,
    *,
    engine: str,
    target: str,
    language: str,
    voices: Mapping[str, str] | None,
    speakers: Mapping[str, str | int] | None,
    aliases: Mapping[str, str] | None,
    options: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[tuple[str, ...], dict[str, RoleVoiceBinding]]:
    role_names = tuple(role.name for role in requirements.roles)
    if not role_names:
        detail = (
            f"; {requirements.implicit_blocks} unvoiced block(s) use the profile default"
            if requirements.uses_default_role
            else ""
        )
        raise ValueError(
            f"source has no explicit voice roles{detail}; add SSMD voice directives or "
            "use 'readio profile create' to create a narrator profile"
        )
    direct = _direct_bindings(
        role_names,
        engine=engine,
        target=target,
        language=language,
        voices=voices or {},
        speakers=speakers or {},
        options=options or {},
    )
    resolved = _resolve_role_bindings(role_names, direct, aliases or {})
    return role_names, resolved


def create_document_profile(
    source: str | Path,
    *,
    engine: str,
    target: str,
    language: str,
    voices: Mapping[str, str] | None = None,
    speakers: Mapping[str, str | int] | None = None,
    aliases: Mapping[str, str] | None = None,
    options: Mapping[str, Mapping[str, Any]] | None = None,
    default_role: str | None = None,
    offline: bool = False,
    refresh: bool = False,
    output: str | Path,
    force: bool = False,
) -> object:
    """Create a complete native VoiceRender profile for an SSMD document."""

    requirements = inspect_document_voice_requirements(source)
    role_names, bindings = _profile_roles(
        requirements,
        engine=engine,
        target=target,
        language=language,
        voices=voices,
        speakers=speakers,
        aliases=aliases,
        options=options,
    )
    selected_default = requirements.default_role_candidate if default_role is None else default_role
    if selected_default not in role_names:
        raise ValueError(f"default role {selected_default!r} is not present in the source role set")

    first = bindings[selected_default]
    profile = voicerender.create_render_profile(
        engine=first.engine,
        target_id=first.target,
        language=first.language,
        role=first.role,
        voice=first.voice,
        speaker=first.speaker,
        options=first.options or {},
        offline=offline,
        refresh=refresh,
    )
    for role in role_names:
        if role == selected_default:
            continue
        binding = bindings[role]
        profile = voicerender.set_profile_role(
            profile,
            role,
            engine=binding.engine,
            target_id=binding.target,
            language=binding.language,
            voice=binding.voice,
            speaker=binding.speaker,
            options=binding.options or {},
            offline=offline,
            refresh=refresh,
        )
    profile = voicerender.validate_render_profile(profile)
    _persist_profile(profile, output, force=force)
    return profile


__all__ = [
    "DocumentVoiceRequirements",
    "DocumentVoiceRole",
    "RoleAlias",
    "RoleVoiceBinding",
    "create_document_profile",
    "inspect_document_voice_requirements",
]
