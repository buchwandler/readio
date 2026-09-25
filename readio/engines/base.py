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
class RequestMeasure:
    """Capacity measurement for one exact Readio speech request."""

    fits: bool | None
    amount: int | None
    maximum: int | None
    unit: Literal["model_tokens", "phoneme_ids", "unknown"]
    source: str
    details: Mapping[str, Any] = field(default_factory=dict)


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

    supports_voice_level_calibration: bool = False
    supports_request_measurement: bool = False


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

    def measure(self, request: SpeechRequest) -> RequestMeasure:
        """Measure request capacity without performing acoustic inference."""
        ...

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


def validate_rendered_speech(
    request: SpeechRequest,
    result: RenderedSpeech,
    *,
    engine: str | None = None,
    engine_version: str | None = None,
    target_id: str | None = None,
) -> RenderedSpeech:
    """Validate and normalize an engine result before Readio consumes it."""
    from numbers import Integral

    import numpy as np

    from ..errors import EngineBackendError

    context = {
        "engine": engine,
        "engine_version": engine_version,
        "target_id": target_id,
        "language": request.language,
        "voice": request.voice,
        "speaker": request.speaker,
        "request_id": request.id,
    }
    context = {key: value for key, value in context.items() if value is not None}

    def invalid(reason: str, cause: Exception | None = None) -> EngineBackendError:
        error = EngineBackendError(
            f"Engine returned invalid rendered speech: {reason}",
            **context,
        )
        if cause is not None:
            error.__cause__ = cause
        return error

    if result.id != request.id:
        raise invalid(f"result id {result.id!r} does not match request id {request.id!r}")
    if isinstance(result.sample_rate, bool) or not isinstance(result.sample_rate, Integral):
        raise invalid("sample rate must be a positive integer")
    sample_rate = int(result.sample_rate)
    if sample_rate <= 0:
        raise invalid("sample rate must be a positive integer")

    try:
        audio = np.asarray(result.audio, dtype=np.float32)
    except (TypeError, ValueError, OverflowError) as exc:
        raise invalid("audio must convert to float32", exc) from exc
    if audio.ndim == 2 and audio.shape[1] == 1:
        audio = audio[:, 0]
    if audio.ndim != 1:
        raise invalid("audio must be mono")
    if audio.size == 0:
        raise invalid("audio must be non-empty")
    if not np.isfinite(audio).all():
        raise invalid("audio must contain only finite samples")

    previous_char_start = -1
    previous_char_end = -1
    previous_sample_start = -1
    previous_sample_end = -1
    for timing in result.word_timings:
        coordinates = (
            timing.char_start,
            timing.char_end,
            timing.start_sample,
            timing.end_sample,
        )
        if any(isinstance(value, bool) or not isinstance(value, Integral) for value in coordinates):
            raise invalid("word timing coordinates must be integers")
        char_start, char_end, sample_start, sample_end = (int(value) for value in coordinates)
        if not (0 <= char_start <= char_end <= len(request.text)):
            raise invalid("word timing character range is outside the request text")
        if not (0 <= sample_start <= sample_end <= audio.size):
            raise invalid("word timing sample range is outside the rendered audio")
        if char_start < previous_char_start or char_end < previous_char_end:
            raise invalid("word timings are not monotonic in request text")
        if sample_start < previous_sample_start or sample_end < previous_sample_end:
            raise invalid("word timings are not monotonic in rendered audio")
        previous_char_start = char_start
        previous_char_end = char_end
        previous_sample_start = sample_start
        previous_sample_end = sample_end

    result.audio = audio
    result.sample_rate = sample_rate
    return result


__all__ = [
    "EngineAdapter",
    "EngineCapabilities",
    "EngineSelection",
    "EngineSession",
    "PronunciationSpan",
    "RenderedSpeech",
    "RequestMeasure",
    "SpeechRequest",
    "SpeechToken",
    "SpeechWordTiming",
    "validate_rendered_speech",
]
