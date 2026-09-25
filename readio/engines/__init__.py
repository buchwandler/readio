"""Neutral engine contracts, registry, and synthesis target discovery."""

from .base import (
    EngineAdapter,
    EngineCapabilities,
    EngineSelection,
    EngineSession,
    PronunciationSpan,
    RenderedSpeech,
    SpeechRequest,
    SpeechToken,
    SpeechWordTiming,
)
from .catalog import SynthesisTarget
from .discovery import discover_targets
from .registry import (
    CANONICAL_ENGINE_IDS,
    ENGINE_ALIASES,
    EngineRegistry,
    default_engine,
    engine_for_ssmd_provider,
    engine_ids,
    engine_status,
    get_engine,
    iter_engines,
    normalize_engine_id,
    ssmd_provider_for_engine,
)

__all__ = [
    "CANONICAL_ENGINE_IDS",
    "ENGINE_ALIASES",
    "EngineAdapter",
    "EngineCapabilities",
    "EngineRegistry",
    "EngineSelection",
    "EngineSession",
    "PronunciationSpan",
    "RenderedSpeech",
    "SpeechRequest",
    "SpeechToken",
    "SpeechWordTiming",
    "SynthesisTarget",
    "default_engine",
    "discover_targets",
    "engine_for_ssmd_provider",
    "engine_ids",
    "engine_status",
    "get_engine",
    "iter_engines",
    "normalize_engine_id",
    "ssmd_provider_for_engine",
]
