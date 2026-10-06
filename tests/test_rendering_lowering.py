from __future__ import annotations

from dataclasses import replace

import pytest
from utterplan import AnnotationSpan, PauseConfig, PlannerConfig, SemanticBoundary, UtterancePlanner

from readio.engines.base import (
    EngineCapabilities,
    EngineSelection,
    PronunciationSpan,
)
from readio.rendering.lowering import LoweringError, lower_segment


def _plan(
    text: str,
    *,
    document_format: str = "plain",
    pause_enabled: bool = True,
):
    return UtterancePlanner(
        PlannerConfig(
            language="en-us",
            document_format=document_format,
            text_preparation="identity",
            unit="sentence",
            pauses=PauseConfig(enabled=pause_enabled),
        )
    ).plan(text)


def _target(language: str = "en-us", **kwargs) -> EngineSelection:
    return EngineSelection(
        engine="pykokoro",
        target_id="model",
        language=language,
        voice="af_sarah",
        **kwargs,
    )


def _capabilities(**kwargs) -> EngineCapabilities:
    values = {
        "id": "pykokoro",
        "supports_named_voices": True,
        "supports_pronunciation_overrides": True,
        "pronunciation_alphabets": frozenset({"ipa"}),
        "supports_linguistic_tokens": True,
        "supports_whole_request_phonemes": True,
    }
    values.update(kwargs)
    return EngineCapabilities(**values)


def test_lower_segment_preserves_text_and_rebases_tokens() -> None:
    plan = _plan("One sentence.\n\nSecond paragraph.")
    segment = plan.segments[1]

    lowered = lower_segment(plan, segment, _target(), _capabilities())

    assert lowered.request.id == segment.id
    assert lowered.request.text == segment.text
    assert lowered.request.language == segment.language
    assert lowered.request.tokens
    for token in lowered.request.tokens:
        assert segment.text[token.start : token.end] == token.text
        assert 0 <= token.start <= token.end <= len(segment.text)


@pytest.mark.parametrize("kind", ["clause", "parenthetical"])
def test_lower_segment_rebases_semantic_boundaries_without_pause_dependency(kind: str) -> None:
    plan = _plan("I wanted to go, but it was raining.", pause_enabled=False)
    segment = plan.segments[0]
    position = segment.spoken_start + len("I wanted to go, ")
    boundary = SemanticBoundary(
        id="semantic-boundary-000000",
        position=position,
        kind=kind,
        origin="phrasplit",
    )
    plan = replace(plan, semantic_boundaries=(boundary,))

    lowered = lower_segment(plan, segment, _target(), _capabilities())

    assert segment.pause_before.seconds == 0
    assert segment.pause_after.seconds == 0
    assert lowered.semantic_boundaries[0].position == len("I wanted to go, ")
    assert lowered.semantic_boundaries[0].kind == kind
    assert lowered.semantic_boundaries[0].semantic_boundary_id == boundary.id
    assert lowered.semantic_boundaries[0].origin == "phrasplit"
    left, right = (
        segment.text[: lowered.semantic_boundaries[0].position],
        segment.text[lowered.semantic_boundaries[0].position :],
    )
    assert left == "I wanted to go, "
    assert right == "but it was raining."
    assert left + right == segment.text


def test_lower_segment_ignores_unknown_future_semantic_kind() -> None:
    plan = _plan("A future boundary should not affect lowering.")
    segment = plan.segments[0]
    boundary = SemanticBoundary(
        id="semantic-boundary-000000",
        position=segment.spoken_start + 2,
        kind="vendor_future_kind",
    )
    plan = replace(plan, semantic_boundaries=(boundary,))

    lowered = lower_segment(plan, segment, _target(), _capabilities())

    assert lowered.semantic_boundaries == ()


def test_lower_segment_rebases_pronunciation_annotations() -> None:
    plan = _plan('[tomato]{ph="təˈmeɪtoʊ" alphabet="ipa"}', document_format="ssmd")
    segment = plan.segments[0]

    lowered = lower_segment(plan, segment, _target(), _capabilities())

    assert lowered.request.pronunciation_overrides == (
        PronunciationSpan(
            start=0,
            end=len(segment.text),
            phonemes="təˈmeɪtoʊ",
            language=None,
            alphabet="ipa",
        ),
    )


def test_lower_segment_rejects_pronunciation_span_crossing_segment() -> None:
    plan = _plan("One. Two.")
    first = plan.segments[0]
    annotation = AnnotationSpan(
        id="phoneme-1",
        kind="phoneme",
        attrs={"ph": "wɜːd", "alphabet": "ipa"},
        structural_start=0,
        structural_end=5,
        spoken_start=0,
        spoken_end=first.spoken_end + 1,
    )
    plan = replace(
        plan,
        annotations=(annotation,),
        segments=(replace(first, annotation_ids=(annotation.id,)), *plan.segments[1:]),
    )

    with pytest.raises(LoweringError, match="crosses segment") as error:
        lower_segment(plan, plan.segments[0], _target(), _capabilities())

    assert error.value.diagnostic.code == "render.pronunciation_span_ambiguous"


def test_lower_segment_rejects_unsupported_pronunciation_alphabet() -> None:
    plan = _plan('[word]{ph="wɜːd" alphabet="x-sampa"}', document_format="ssmd")

    with pytest.raises(LoweringError) as error:
        lower_segment(plan, plan.segments[0], _target(), _capabilities())

    assert error.value.diagnostic.code == "render.pronunciation_alphabet_unsupported"


def test_lower_segment_rejects_unresolved_explicit_voice() -> None:
    plan = _plan('[Hello]{voice="narrator"}', document_format="ssmd")
    target = replace(_target(), voice=None)

    with pytest.raises(LoweringError) as error:
        lower_segment(plan, plan.segments[0], target, _capabilities())

    assert error.value.diagnostic.code == "render.voice_unresolved"


def test_lower_segment_reports_unsupported_linguistic_tokens() -> None:
    plan = _plan("Hello there.")
    capabilities = _capabilities(supports_linguistic_tokens=False)

    lowered = lower_segment(plan, plan.segments[0], _target(), capabilities)

    assert lowered.request.tokens == ()
    assert lowered.capacity_token_ranges
    assert all(
        0 <= start < end <= len(lowered.request.text)
        for start, end in lowered.capacity_token_ranges
    )
    assert lowered.diagnostics[0].code == "render.linguistic_tokens_unsupported"


def test_lower_segment_rejects_incompatible_language() -> None:
    plan = _plan('[Bonjour]{lang="fr"}', document_format="ssmd")
    target = _target(language="en-us")

    with pytest.raises(LoweringError) as error:
        lower_segment(plan, plan.segments[0], target, _capabilities())

    assert error.value.diagnostic.code == "render.engine_language_incompatible"
