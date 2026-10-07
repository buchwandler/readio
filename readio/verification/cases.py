"""Versioned, deterministic source-text cases for Redux self-tests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_TEXT = (
    "Clear speech makes long listening easier. This benchmark checks planning,\n"
    "synthesis, composition, and transcription from beginning to end. The final\n"
    "recording should contain every sentence in the same order, with no missing\n"
    "words, repeated phrases, or unexpected speech."
)


@dataclass(frozen=True, slots=True)
class VerificationCase:
    id: str
    text: str
    expected_text: str | None = None
    language: str = "en-us"

    @property
    def reference_text(self) -> str:
        return self.expected_text if self.expected_text is not None else self.text


READIO_E2E_CASE = VerificationCase("readio-e2e-en-v1", DEFAULT_TEXT)
POCKET_SHORT_TAIL_CASE = VerificationCase(
    "pocket-short-tail-v1",
    "Hello, how are you?",
    expected_text="Hello, how are you?",
    language="en",
)


READIO_TIMESTAMPS_CASE = VerificationCase(
    "readio-timestamps-en-v1",
    "Time tests compare words and retain exact source spans. Words repeat: time tests compare words.",
)
_CASES: Mapping[str, VerificationCase] = {
    READIO_E2E_CASE.id: READIO_E2E_CASE,
    "default": READIO_E2E_CASE,
    "redux-e2e-fixed-english": READIO_E2E_CASE,
    POCKET_SHORT_TAIL_CASE.id: POCKET_SHORT_TAIL_CASE,
    "pocket-short-tail": POCKET_SHORT_TAIL_CASE,
    READIO_TIMESTAMPS_CASE.id: READIO_TIMESTAMPS_CASE,
}


def get_case(case: str) -> VerificationCase:
    try:
        return _CASES[case]
    except KeyError as error:
        raise ValueError(
            f"unknown verification case {case!r}; choose from {tuple(_CASES)}"
        ) from error


def list_cases() -> tuple[VerificationCase, ...]:
    return (READIO_E2E_CASE, POCKET_SHORT_TAIL_CASE, READIO_TIMESTAMPS_CASE)


__all__ = [
    "DEFAULT_TEXT",
    "POCKET_SHORT_TAIL_CASE",
    "READIO_E2E_CASE",
    "READIO_TIMESTAMPS_CASE",
    "VerificationCase",
    "get_case",
    "list_cases",
]
