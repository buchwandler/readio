"""Engine-qualified voice targets used by role bindings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .engines.registry import (
    ONNXVOICE_SYSTEM_TO_READIO_ENGINE,
    normalize_engine_id,
    ssmd_provider_for_engine,
)

_PROVIDER_BY_ENGINE = {
    engine: provider for provider, engine in ONNXVOICE_SYSTEM_TO_READIO_ENGINE.items()
}


@dataclass(frozen=True, slots=True)
class VoiceTarget:
    engine: str
    voice: str
    target_id: str | None = None
    selector: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.engine, str) or not isinstance(self.voice, str):
            raise TypeError("voice target engine and voice must be strings")
        engine = normalize_engine_id(self.engine.strip())
        voice = self.voice.strip()
        if not engine:
            raise ValueError("voice target engine must be a non-empty string")
        if not voice:
            raise ValueError("voice target voice must be a non-empty string")
        object.__setattr__(self, "engine", engine)
        object.__setattr__(self, "voice", voice)
        for name in ("target_id", "selector"):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError(f"voice target {name} must be a non-empty string or None")
                value = value.strip()
                if not value:
                    raise ValueError(f"voice target {name} must be a non-empty string or None")
                object.__setattr__(self, name, value)

    @property
    def provider(self) -> str | None:
        provider = _PROVIDER_BY_ENGINE.get(self.engine)
        if provider is not None:
            return provider
        try:
            return ssmd_provider_for_engine(self.engine)
        except ValueError:
            return None

    def to_dict(self) -> dict[str, str]:
        result = {"engine": self.engine, "voice": self.voice}
        if self.target_id is not None:
            result["target_id"] = self.target_id
        if self.selector is not None:
            result["selector"] = self.selector
        return result


def voice_target_from_mapping(value: object, *, name: str = "voice target") -> VoiceTarget:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    engine = value.get("engine")
    voice = value.get("voice")
    if not isinstance(engine, str) or not engine.strip():
        raise ValueError(f"{name}.engine must be a non-empty string")
    if not isinstance(voice, str) or not voice.strip():
        raise ValueError(f"{name}.voice must be a non-empty string")
    optional: dict[str, str | None] = {}
    for field in ("target_id", "selector"):
        item = value.get(field)
        if item is not None and (not isinstance(item, str) or not item.strip()):
            raise ValueError(f"{name}.{field} must be a non-empty string or None")
        optional[field] = item
    return VoiceTarget(engine=engine, voice=voice, **optional)


__all__ = ["VoiceTarget", "voice_target_from_mapping"]
