"""Lower UtterPlan segments into Readio-owned engine requests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from utterplan import PlanSegment, SemanticBoundary, UtterancePlan

from ..engines.base import (
    EngineCapabilities,
    EngineSelection,
    PronunciationSpan,
    SpeechRequest,
    SpeechToken,
)
from ..plan import PlanDiagnostic


@dataclass(frozen=True, slots=True)
class RequestBoundary:
    """UtterPlan semantic split hint rebased to a single speech request."""

    position: int
    kind: str
    semantic_boundary_id: str
    origin: str


@dataclass(frozen=True, slots=True)
class LoweredSegment:
    """Synthesis request plus semantic context retained for composition."""

    request: SpeechRequest
    segment_id: str
    unit_id: str | None
    pause_before: float
    pause_after: float
    directives: Mapping[str, Any]
    marker_ids: tuple[str, ...] = ()
    semantic_boundaries: tuple[RequestBoundary, ...] = ()
    capacity_token_ranges: tuple[tuple[int, int], ...] = ()
    diagnostics: tuple[PlanDiagnostic, ...] = ()


class LoweringError(ValueError):
    """Explicit semantic input could not be represented by the selected target."""

    def __init__(self, diagnostic: PlanDiagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


def lower_segment(
    plan: UtterancePlan,
    segment: PlanSegment,
    target: EngineSelection,
    capabilities: EngineCapabilities,
    *,
    strict: bool = False,
) -> LoweredSegment:
    """Convert one semantic segment to a neutral synthesis request.

    UtterPlan offsets are global to the spoken document. Request annotations are
    rebased to the segment's text and checked against the exact source slice.
    """
    _require_compatible_language(segment.language, target)
    voice = target.voice
    voice_directive = segment.directives.voice
    if voice_directive is not None and voice is None:
        _fail(
            "render.voice_unresolved",
            f"Voice directive on segment {segment.id!r} has no resolved concrete voice target.",
            "render.target.voice",
        )

    diagnostics: list[PlanDiagnostic] = []
    source_tokens = plan.tokens_for_segment(segment)
    capacity_token_ranges = _collect_capacity_token_ranges(segment, source_tokens)
    semantic_boundaries = _collect_semantic_boundaries(plan, segment)
    if source_tokens and not capabilities.supports_linguistic_tokens:
        tokens = ()
        diagnostics.append(
            PlanDiagnostic(
                code="render.linguistic_tokens_unsupported",
                severity="warning",
                message=f"Engine {capabilities.id!r} cannot consume linguistic tokens for segment {segment.id!r}.",
                field="render.segment.tokens",
            )
        )
    else:
        tokens = _collect_tokens(plan, segment)
    overrides, whole_phonemes = _collect_pronunciation(plan, segment, capabilities)
    directives = segment.directives.to_dict()
    if segment.directives.emphasis is not None:
        diagnostics.append(
            PlanDiagnostic(
                code="render.emphasis_unmapped",
                severity="warning",
                message=f"Emphasis on segment {segment.id!r} has no configured composition mapping.",
                field="render.segment.emphasis",
            )
        )
    if segment.directives.audio is not None:
        _fail(
            "render.external_audio_unsupported",
            f"External audio on segment {segment.id!r} must be handled by composition, not speech synthesis.",
            "render.segment.audio",
        )
    for extension in segment.directives.extensions:
        severity = "error" if strict else "warning"
        diagnostic = PlanDiagnostic(
            code="render.extension_unsupported",
            severity=severity,
            message=(
                f"Unknown SSMD extension {extension.name!r} on segment {segment.id!r} "
                "is not interpreted by the selected synthesis engine."
            ),
            field="render.segment.extensions",
        )
        if strict:
            raise LoweringError(diagnostic)
        diagnostics.append(diagnostic)

    request = SpeechRequest(
        id=segment.id,
        text=segment.text,
        language=segment.language,
        voice=voice,
        speaker=target.speaker,
        pronunciation_overrides=overrides,
        tokens=tokens,
        whole_request_phonemes=whole_phonemes,
        options=dict(target.options),
    )
    unit_id = next(
        (unit.id for unit in plan.units if segment.id in unit.segment_ids),
        None,
    )
    marker_ids = tuple(
        marker.id
        for marker in plan.markers
        if segment.spoken_start <= marker.spoken_position <= segment.spoken_end
    )
    return LoweredSegment(
        request=request,
        segment_id=segment.id,
        unit_id=unit_id,
        pause_before=segment.pause_before.seconds,
        pause_after=segment.pause_after.seconds,
        directives=directives,
        marker_ids=marker_ids,
        semantic_boundaries=semantic_boundaries,
        capacity_token_ranges=capacity_token_ranges,
        diagnostics=tuple(diagnostics),
    )


def _collect_tokens(
    plan: UtterancePlan,
    segment: PlanSegment,
) -> tuple[SpeechToken, ...]:
    lowered: list[SpeechToken] = []
    for token in plan.tokens_for_segment(segment):
        start = token.spoken_start - segment.spoken_start
        end = token.spoken_end - segment.spoken_start
        if (
            start < 0
            or start > end
            or end > len(segment.text)
            or segment.text[start:end] != token.text
        ):
            _fail(
                "render.token_offset_invalid",
                f"Token offsets do not match segment {segment.id!r} text.",
                "render.segment.tokens",
            )
        lowered.append(
            SpeechToken(
                start=start,
                end=end,
                text=token.text,
                pos=token.pos,
                tag=token.tag,
                lemma=token.lemma,
                morph=token.morph,
                language=token.language,
            )
        )
    return tuple(lowered)


def _collect_capacity_token_ranges(
    segment: PlanSegment,
    source_tokens: tuple[Any, ...],
) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    for token in source_tokens:
        start = token.spoken_start - segment.spoken_start
        end = token.spoken_end - segment.spoken_start
        if (
            start < 0
            or start >= end
            or end > len(segment.text)
            or segment.text[start:end] != token.text
        ):
            _fail(
                "render.token_offset_invalid",
                f"Token offsets do not match segment {segment.id!r} text.",
                "render.segment.tokens",
            )
        ranges.append((start, end))
    return tuple(ranges)


def _collect_semantic_boundaries(
    plan: UtterancePlan,
    segment: PlanSegment,
) -> tuple[RequestBoundary, ...]:
    supported_kinds = {"clause", "parenthetical"}
    boundaries: tuple[SemanticBoundary, ...] = plan.semantic_boundaries_for_segment(
        segment,
        kinds=supported_kinds,
    )
    result: list[RequestBoundary] = []
    for boundary in boundaries:
        if boundary.kind not in supported_kinds:
            continue
        position = boundary.position - segment.spoken_start
        if type(position) is not int or not 0 < position < len(segment.text):
            _fail(
                "render.semantic_boundary_offset_invalid",
                f"Semantic boundary {boundary.id!r} does not rebase inside segment {segment.id!r}.",
                "render.segment.semantic_boundaries",
            )
        result.append(
            RequestBoundary(
                position=position,
                kind=boundary.kind,
                semantic_boundary_id=boundary.id,
                origin=boundary.origin,
            )
        )
    return tuple(result)


def _collect_pronunciation(
    plan: UtterancePlan,
    segment: PlanSegment,
    capabilities: EngineCapabilities,
) -> tuple[tuple[PronunciationSpan, ...], str | None]:
    annotation_ids = set(segment.annotation_ids)
    directives = segment.directives.pronunciation
    annotations = tuple(
        annotation
        for annotation in plan.annotations
        if annotation.kind in {"phoneme", "pronunciation"}
        and (
            annotation.id in annotation_ids
            or (
                annotation.spoken_start is not None
                and annotation.spoken_end is not None
                and annotation.spoken_start < segment.spoken_end
                and annotation.spoken_end > segment.spoken_start
            )
        )
    )
    if directives is not None and not annotations:
        _fail(
            "render.pronunciation_offsets_missing",
            f"Pronunciation directive on segment {segment.id!r} has no source-aligned annotation.",
            "render.segment.pronunciation",
        )

    spans: list[PronunciationSpan] = []
    whole_request_phonemes: str | None = None
    for annotation in annotations:
        start = annotation.spoken_start
        end = annotation.spoken_end
        if start is None or end is None:
            _fail(
                "render.pronunciation_offsets_missing",
                f"Pronunciation annotation {annotation.id!r} has no spoken-text offsets.",
                "render.segment.pronunciation",
            )
        if end <= segment.spoken_start or start >= segment.spoken_end:
            continue
        if start < segment.spoken_start or end > segment.spoken_end:
            _fail(
                "render.pronunciation_span_ambiguous",
                f"Pronunciation annotation {annotation.id!r} crosses segment {segment.id!r} boundaries.",
                "render.segment.pronunciation",
            )
        attrs = annotation.attrs
        phonemes = attrs.get("ph", attrs.get("phonemes"))
        if not isinstance(phonemes, str) or not phonemes:
            _fail(
                "render.pronunciation_invalid",
                f"Pronunciation annotation {annotation.id!r} has no phoneme value.",
                "render.segment.pronunciation",
            )
        alphabet = str(attrs.get("alphabet", "ipa")).lower()
        if alphabet not in capabilities.pronunciation_alphabets:
            _fail(
                "render.pronunciation_alphabet_unsupported",
                f"Engine {capabilities.id!r} does not support pronunciation alphabet {alphabet!r}.",
                "render.segment.pronunciation.alphabet",
            )
        if not capabilities.supports_pronunciation_overrides:
            if (
                capabilities.supports_whole_request_phonemes
                and start == segment.spoken_start
                and end == segment.spoken_end
                and whole_request_phonemes is None
            ):
                whole_request_phonemes = phonemes
                continue
            _fail(
                "render.pronunciation_unsupported",
                f"Engine {capabilities.id!r} cannot represent pronunciation annotation {annotation.id!r}.",
                "render.segment.pronunciation",
            )
        spans.append(
            PronunciationSpan(
                start=start - segment.spoken_start,
                end=end - segment.spoken_start,
                phonemes=phonemes,
                language=attrs.get("language"),
                alphabet=alphabet,
            )
        )
    return tuple(spans), whole_request_phonemes


def _require_compatible_language(language: str, target: EngineSelection) -> None:
    declared = target.metadata.get("languages") or target.metadata.get("supported_languages")
    if isinstance(declared, str):
        languages = (declared,)
    else:
        languages = tuple(declared) if declared else (target.language,)
    if not any(_same_language(language, supported) for supported in languages):
        _fail(
            "render.engine_language_incompatible",
            f"Target {target.target_id!r} does not support segment language {language!r}.",
            "render.target.language",
        )


def _same_language(left: str, right: str) -> bool:
    left_key = left.lower().replace("_", "-")
    right_key = right.lower().replace("_", "-")
    return left_key == right_key or left_key.split("-", 1)[0] == right_key.split("-", 1)[0]


def _fail(code: str, message: str, field: str) -> None:
    raise LoweringError(PlanDiagnostic(code=code, severity="error", message=message, field=field))


__all__ = ["LoweredSegment", "LoweringError", "RequestBoundary", "lower_segment"]
