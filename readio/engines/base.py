"""Engine-neutral synthesis contracts owned by Readio."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from .catalog import CatalogRequest, SynthesisTarget
    from .selection import EngineRequest


@dataclass(frozen=True, slots=True)
class SpeechToken:
    """One linguistic token with offsets local to a synthesis request."""

    start: int
    end: int
    text: str
    pos: str | None = None
    tag: str | None = None
    lemma: str | None = None
    morph: str | None = None
    language: str | None = None


@dataclass(frozen=True, slots=True)
class PronunciationSpan:
    """A request-local pronunciation override."""

    start: int
    end: int
    phonemes: str | None = None
    language: str | None = None
    alphabet: str | None = None


@dataclass(frozen=True, slots=True)
class SpeechRequest:
    """Prepared text and optional linguistic context for one engine synthesis."""

    id: str
    text: str
    language: str
    voice: str | None = None
    speaker: str | int | None = None
    pronunciation_overrides: tuple[PronunciationSpan, ...] = ()
    tokens: tuple[SpeechToken, ...] = ()
    whole_request_phonemes: str | None = None
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SpeechWordTiming:
    """A word timing expressed in request text and rendered-audio coordinates."""

    text: str
    char_start: int
    char_end: int
    start_sample: int
    end_sample: int


@dataclass(slots=True)
class RenderedSpeech:
    """Mono float32 audio and metadata for one independent speech request."""

    id: str
    audio: Any
    sample_rate: int
    warnings: tuple[str, ...] = ()
    word_timings: tuple[SpeechWordTiming, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EngineCapabilities:
    """Synthesis features an engine adapter can represent natively."""

    id: str
    voice_binding_namespace: str | None = None
    voice_binding_scope: Literal["request", "target"] = "request"
    option_names: frozenset[str] = frozenset()
    supports_named_voices: bool = False
    supports_reference_voice: bool = False
    supports_speakers: bool = False
    supports_pronunciation_overrides: bool = False
    pronunciation_alphabets: frozenset[str] = frozenset()
    supports_linguistic_tokens: bool = False
    supports_whole_request_phonemes: bool = False
    supports_lexicons: bool = False
    supports_model_sources: bool = False
    supports_qualities: bool = False
    supports_live: bool = False
    supports_timestamps: bool = False


@dataclass(frozen=True, slots=True)
class EngineSelection:
    """Concrete engine selection produced during plan resolution."""

    engine: str
    target_id: str
    language: str
    voice: str | None = None
    speaker: str | int | None = None
    options: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    offline: bool = False
    refresh: bool = False


class EngineSession(Protocol):
    """Open synthesis session that renders one Readio speech request at a time."""

    def synthesize(self, request: SpeechRequest) -> RenderedSpeech:
        """Synthesize one independent request without composing other requests."""
        ...


class EngineAdapter(Protocol):
    """Discovery, target resolution, and session opening for one synthesis engine."""

    id: str

    def version(self) -> str | None:
        """Return the installed engine package version, or None."""
        ...

    def capabilities(self) -> EngineCapabilities:
        """Return the synthesis features supported by this adapter."""
        ...

    def discover(
        self,
        request: CatalogRequest,
    ) -> tuple[SynthesisTarget, ...]:
        """Discover available targets for this engine."""
        ...

    def resolve(
        self,
        request: EngineRequest,
    ) -> tuple[EngineSelection, tuple[Any, ...]]:
        """Resolve and validate a concrete engine selection."""
        ...

    def canonical_synthesis_identity(
        self,
        selection: EngineSelection,
    ) -> Mapping[str, Any]:
        """Return identity inputs for canonical speech synthesis."""
        ...

    def open(
        self,
        selection: EngineSelection,
    ) -> AbstractContextManager[EngineSession]:
        """Open a rendering session for the given concrete target."""
        ...


__all__ = [
    "EngineAdapter",
    "EngineCapabilities",
    "EngineSelection",
    "EngineSession",
    "PronunciationSpan",
    "RenderedSpeech",
    "SpeechRequest",
    "SpeechToken",
    "SpeechWordTiming",
]
