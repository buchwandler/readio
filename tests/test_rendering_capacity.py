from __future__ import annotations

import numpy as np
import pytest

from readio.engines.base import (
    PronunciationSpan,
    RenderedSpeech,
    RequestMeasure,
    SpeechRequest,
    SpeechToken,
    SpeechWordTiming,
)
from readio.errors import EngineBackendError, SpeechRequestTooLongError
from readio.rendering import render_atomic_request


class _MeasuredSession:
    def __init__(self, maximum: int, *, sample_rate: int = 24_000) -> None:
        self.maximum = maximum
        self.sample_rate = sample_rate
        self.calls: list[SpeechRequest] = []

    def measure(self, request: SpeechRequest) -> RequestMeasure:
        amount = len(request.text)
        return RequestMeasure(
            fits=amount <= self.maximum,
            amount=amount,
            maximum=self.maximum,
            unit="model_tokens",
            source="test.length",
        )

    def synthesize(self, request: SpeechRequest) -> RenderedSpeech:
        self.calls.append(request)
        assert len(request.text) <= self.maximum
        return _rendered(request, self.sample_rate, len(self.calls))


class _UnknownSession(_MeasuredSession):
    def measure(self, request: SpeechRequest) -> RequestMeasure:
        return RequestMeasure(
            fits=None,
            amount=None,
            maximum=None,
            unit="unknown",
            source="test.unknown",
        )

    def synthesize(self, request: SpeechRequest) -> RenderedSpeech:
        self.calls.append(request)
        if len(request.text) > self.maximum:
            raise SpeechRequestTooLongError(
                "request exceeds fake limit",
                request_id=request.id,
                amount=len(request.text),
                maximum=self.maximum,
                unit="model_tokens",
                text_length=len(request.text),
            )
        return _rendered(request, self.sample_rate, len(self.calls))


def _rendered(request: SpeechRequest, sample_rate: int, value: int) -> RenderedSpeech:
    audio = np.full(len(request.text), value, dtype=np.float32)
    start = len(request.text) - len(request.text.lstrip())
    end = len(request.text.rstrip())
    timings = ()
    if start < end:
        timings = (
            SpeechWordTiming(
                text=request.text[start:end],
                char_start=start,
                char_end=end,
                start_sample=0,
                end_sample=len(audio),
            ),
        )
    return RenderedSpeech(
        id=request.id,
        audio=audio,
        sample_rate=sample_rate,
        warnings=("shared warning", f"render {value}"),
        word_timings=timings,
        metadata={"child_value": value},
    )


def _request(text: str, **kwargs) -> SpeechRequest:
    return SpeechRequest(id="segment-1", text=text, language="en", **kwargs)


def test_capacity_fitting_prefers_sentence_boundaries_and_preserves_exact_text():
    request = _request("Hello. World is a test.")
    session = _MeasuredSession(10)

    rendered = render_atomic_request(session, request)

    assert [child.text for child in session.calls] == ["Hello. ", "World is ", "a test."]
    assert "".join(child.text for child in session.calls) == request.text
    assert rendered.result.id == request.id
    assert [item.char_start for item in rendered.requests] == [0, 7, 16]
    assert [item.char_end for item in rendered.requests] == [7, 16, len(request.text)]
    assert rendered.result.metadata["atomic_lowering"]["requests"][0]["text"] == "Hello. "


def test_fitting_request_is_rendered_as_one_unchanged_child():
    request = _request("  already fits.  ")
    session = _MeasuredSession(100)

    rendered = render_atomic_request(session, request)

    assert len(session.calls) == 1
    assert session.calls[0].text == request.text
    assert rendered.requests[0].request.text == request.text
    assert rendered.requests[0].request.id == request.id


def test_capacity_fitting_preserves_newline_and_tab_whitespace():
    request = _request("first line \t\n\nsecond line")
    session = _MeasuredSession(15)

    rendered = render_atomic_request(session, request)

    assert [child.text for child in session.calls] == ["first line \t\n\n", "second line"]
    assert "".join(child.text for child in session.calls) == request.text
    assert [(item.char_start, item.char_end) for item in rendered.requests] == [
        (0, 14),
        (14, len(request.text)),
    ]


def test_capacity_fitting_uses_clause_fallback_before_words():
    request = _request("First clause, second clause, final words.")
    session = _MeasuredSession(15)

    rendered = render_atomic_request(session, request)

    assert session.calls[0].text == "First clause, "
    assert all("," in child.text for child in session.calls[:-1])
    assert "".join(child.text for child in session.calls) == request.text
    assert all(item.measure.fits is True for item in rendered.requests)


def test_capacity_fitting_preserves_leading_trailing_and_repeated_whitespace():
    request = _request("  alpha \t beta  ")
    session = _MeasuredSession(10)

    rendered = render_atomic_request(session, request)

    assert [child.text for child in session.calls] == ["  alpha \t ", "beta  "]
    assert "".join(child.text for child in session.calls) == request.text
    assert rendered.requests[-1].char_end == len(request.text)


def test_capacity_fitting_rebases_complete_tokens_and_pronunciation_spans():
    request = _request(
        "red blue green",
        tokens=(SpeechToken(start=4, end=8, text="blue", lemma="blue"),),
        pronunciation_overrides=(
            PronunciationSpan(start=4, end=8, phonemes="bluː", alphabet="ipa"),
        ),
    )
    session = _MeasuredSession(8)

    rendered = render_atomic_request(session, request)

    assert "".join(child.text for child in session.calls) == request.text
    assert all(
        token.start == 0 or token.end == len(child.text)
        for child in session.calls
        for token in child.tokens
    )
    token_child = next(child for child in session.calls if child.tokens)
    assert token_child.tokens[0].text == "blue"
    assert token_child.tokens[0].start == token_child.pronunciation_overrides[0].start
    assert token_child.tokens[0].end == token_child.pronunciation_overrides[0].end
    assert rendered.requests[0].char_start == 0


def test_unknown_measurement_uses_typed_too_long_fallback_and_merges_timings():
    request = _request("alpha beta gamma")
    session = _UnknownSession(6)

    rendered = render_atomic_request(session, request)

    assert [len(call.text) for call in session.calls] == [16, 6, 10, 5, 5]
    assert [item.request.text for item in rendered.requests] == ["alpha ", "beta ", "gamma"]
    assert "".join(item.request.text for item in rendered.requests) == request.text
    assert rendered.result.audio.size == len(request.text)
    assert rendered.result.word_timings[-1].char_start == request.text.index("gamma")
    assert rendered.result.word_timings[-1].start_sample == 11
    assert rendered.result.warnings[0] == "shared warning"
    assert rendered.result.warnings.count("shared warning") == 1
    assert len(rendered.requests) == 3


def test_oversized_whole_request_phonemes_are_not_split():
    request = _request("alpha beta", whole_request_phonemes="ɑl.fə beɪ.tə")
    session = _MeasuredSession(5)

    with pytest.raises(SpeechRequestTooLongError, match="cannot be subdivided safely"):
        render_atomic_request(session, request)

    assert session.calls == []


def test_protected_range_without_a_legal_boundary_fails_before_inference():
    request = _request("longword")
    session = _MeasuredSession(4)

    with pytest.raises(SpeechRequestTooLongError, match="no legal source-text split boundary"):
        render_atomic_request(session, request, protected_ranges=((0, len(request.text)),))

    assert session.calls == []


def test_sample_rate_mismatch_between_children_is_a_typed_backend_error():
    request = _request("alpha beta")
    session = _MeasuredSession(6)
    calls: list[SpeechRequest] = []

    def synthesize(request: SpeechRequest) -> RenderedSpeech:
        calls.append(request)
        rate = 22_050 if request.text.startswith("alpha") else 24_000
        return _rendered(request, rate, len(calls))

    session.synthesize = synthesize

    with pytest.raises(EngineBackendError, match="different sample rates"):
        render_atomic_request(session, request)

    assert [call.text for call in calls] == ["alpha ", "beta"]
