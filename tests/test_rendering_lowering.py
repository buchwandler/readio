from __future__ import annotations

from dataclasses import replace

import pytest
from utterplan import AnnotationSpan, PlannerConfig, UtterancePlanner

from readio.engines.base import (
    EngineCapabilities,
    EngineSelection,
    PronunciationSpan,
)
from readio.rendering.lowering import LoweringError, lower_segment


def _plan(text: str, *, document_format: str = "plain"):
    return UtterancePlanner(
        PlannerConfig(
            language="en-us",
            document_format=document_format,
            text_preparation="identity",
            unit="sentence",
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
    assert lowered.diagnostics[0].code == "render.linguistic_tokens_unsupported"


def test_lower_segment_rejects_incompatible_language() -> None:
    plan = _plan('[Bonjour]{lang="fr"}', document_format="ssmd")
    target = _target(language="en-us")

    with pytest.raises(LoweringError) as error:
        lower_segment(plan, plan.segments[0], target, _capabilities())

    assert error.value.diagnostic.code == "render.engine_language_incompatible"
