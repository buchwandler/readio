"""Semantic, user-facing references for engine voice identities."""

from __future__ import annotations

from dataclasses import dataclass

from .engines.registry import (
    ONNXVOICE_SYSTEM_TO_READIO_ENGINE,
    READIO_ENGINE_TO_ONNXVOICE_SYSTEM,
    normalize_engine_id,
)

_PUBLIC_SYSTEMS = frozenset(ONNXVOICE_SYSTEM_TO_READIO_ENGINE)


@dataclass(frozen=True, slots=True)
class VoiceRef:
    """A public engine, exact target ID, and optional child voice ID."""

    system: str
    target_id: str
    voice_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.system, str):
            raise TypeError("voice reference system must be a string")
        system = self.system.strip().casefold()
        if system not in _PUBLIC_SYSTEMS:
            raise ValueError(f"unsupported voice reference system {self.system!r}")
        object.__setattr__(self, "system", system)
        if not isinstance(self.target_id, str) or not self.target_id.strip():
            raise ValueError("voice reference target ID must be a non-empty string")
        if self.voice_id is not None and (
            not isinstance(self.voice_id, str) or not self.voice_id.strip()
        ):
            raise ValueError("voice reference voice ID must be a non-empty string or None")

    @property
    def value(self) -> str:
        return format_voice_ref(self)


def parse_voice_ref(value: str) -> VoiceRef:
    """Parse ``SYSTEM:TARGET[/VOICE]`` without rewriting target or voice IDs."""
    if not isinstance(value, str):
        raise TypeError("voice reference must be a string")
    if value.count(":") != 1:
        raise ValueError(f"invalid voice reference {value!r}; expected SYSTEM:TARGET[/VOICE]")
    system, identity = value.split(":", 1)
    if not system or not identity:
        raise ValueError(f"invalid voice reference {value!r}; expected SYSTEM:TARGET[/VOICE]")
    if identity.count("/") > 1:
        raise ValueError(f"invalid voice reference {value!r}; expected SYSTEM:TARGET[/VOICE]")
    if "/" in identity:
        target_id, voice_id = identity.split("/", 1)
    else:
        target_id, voice_id = identity, None
    return VoiceRef(system=system, target_id=target_id, voice_id=voice_id)


def format_voice_ref(reference: VoiceRef) -> str:
    """Format a semantic reference using its normalized public system name."""
    if not isinstance(reference, VoiceRef):
        raise TypeError("reference must be a VoiceRef")
    suffix = f"/{reference.voice_id}" if reference.voice_id is not None else ""
    return f"{reference.system}:{reference.target_id}{suffix}"


def is_voice_ref(value: object) -> bool:
    """Return whether *value* is a valid semantic voice reference."""
    if not isinstance(value, str):
        return False
    try:
        parse_voice_ref(value)
    except (TypeError, ValueError):
        return False
    return True


def public_system_for_engine(engine: str) -> str:
    """Return the public reference system for an internal engine ID or alias."""
    canonical_engine = normalize_engine_id(engine.strip().casefold())
    try:
        return READIO_ENGINE_TO_ONNXVOICE_SYSTEM[canonical_engine]
    except KeyError as exc:
        raise ValueError(f"unsupported voice reference engine {engine!r}") from exc


def engine_for_public_system(system: str) -> str:
    """Return the internal engine ID for a public reference system."""
    if not isinstance(system, str):
        raise TypeError("voice reference system must be a string")
    normalized = system.strip().casefold()
    try:
        return ONNXVOICE_SYSTEM_TO_READIO_ENGINE[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported voice reference system {system!r}") from exc


__all__ = [
    "VoiceRef",
    "engine_for_public_system",
    "format_voice_ref",
    "is_voice_ref",
    "parse_voice_ref",
    "public_system_for_engine",
]
