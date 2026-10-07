"""Deterministic transcript normalization and text-fidelity metrics."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

VerificationStatus = Literal["pass", "review", "fail"]


@dataclass(frozen=True, slots=True)
class VerificationThresholds:
    pass_wer: float = 0.10
    pass_cer: float = 0.05
    fail_wer: float = 0.20
    fail_cer: float = 0.10

    def __post_init__(self) -> None:
        values = (self.pass_wer, self.pass_cer, self.fail_wer, self.fail_cer)
        if any(not 0 <= value <= 1 for value in values):
            raise ValueError("WER/CER thresholds must be between 0 and 1")
        if self.pass_wer > self.fail_wer or self.pass_cer > self.fail_cer:
            raise ValueError("pass thresholds must not exceed fail thresholds")


_DEFAULT_THRESHOLDS = VerificationThresholds()


@dataclass(frozen=True, slots=True)
class EditDistance:
    distance: int
    substitutions: int
    deletions: int
    insertions: int
    rate: float


@dataclass(frozen=True, slots=True)
class TextVerificationResult:
    expected: str
    transcript: str
    normalized_expected: str
    normalized_transcript: str
    wer: float
    cer: float
    wer_distance: int
    wer_substitutions: int
    wer_deletions: int
    wer_insertions: int
    cer_distance: int
    status: VerificationStatus


def normalize_text(value: str) -> str:
    """Normalize formatting while retaining spoken words and apostrophes."""
    apostrophes = str.maketrans(
        {
            "’": "'",
            "‘": "'",
            "‛": "'",
            "ʼ": "'",
            "＇": "'",
            "`": "'",
            "´": "'",
        }
    )
    value = unicodedata.normalize("NFKC", value).casefold().translate(apostrophes)
    value = re.sub(r"[^\w\s']", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def edit_distance(reference: Sequence[Any], hypothesis: Sequence[Any]) -> EditDistance:
    """Levenshtein distance with a deterministic substitution/deletion/insertion trace."""
    ref_len, hyp_len = len(reference), len(hypothesis)
    costs = [[0] * (hyp_len + 1) for _ in range(ref_len + 1)]
    for row in range(1, ref_len + 1):
        costs[row][0] = row
    for column in range(1, hyp_len + 1):
        costs[0][column] = column
    for row in range(1, ref_len + 1):
        for column in range(1, hyp_len + 1):
            substitution = 0 if reference[row - 1] == hypothesis[column - 1] else 1
            costs[row][column] = min(
                costs[row - 1][column] + 1,
                costs[row][column - 1] + 1,
                costs[row - 1][column - 1] + substitution,
            )
    row, column = ref_len, hyp_len
    substitutions = deletions = insertions = 0
    while row or column:
        if (
            row
            and column
            and reference[row - 1] == hypothesis[column - 1]
            and costs[row][column] == costs[row - 1][column - 1]
        ):
            row -= 1
            column -= 1
        elif row and column and costs[row][column] == costs[row - 1][column - 1] + 1:
            substitutions += 1
            row -= 1
            column -= 1
        elif row and costs[row][column] == costs[row - 1][column] + 1:
            deletions += 1
            row -= 1
        else:
            insertions += 1
            column -= 1
    distance = costs[ref_len][hyp_len]
    rate = distance / ref_len if ref_len else (0.0 if not hyp_len else 1.0)
    return EditDistance(distance, substitutions, deletions, insertions, rate)


def word_error_rate(reference: str, hypothesis: str) -> float:
    return edit_distance(normalize_text(reference).split(), normalize_text(hypothesis).split()).rate


def character_error_rate(reference: str, hypothesis: str) -> float:
    ref = normalize_text(reference).replace(" ", "")
    hyp = normalize_text(hypothesis).replace(" ", "")
    return edit_distance(ref, hyp).rate


def classify_verification(
    *,
    wer: float,
    cer: float,
    transcript: str,
    thresholds: VerificationThresholds = _DEFAULT_THRESHOLDS,
) -> VerificationStatus:
    if not normalize_text(transcript):
        return "fail"
    if wer <= thresholds.pass_wer and cer <= thresholds.pass_cer:
        return "pass"
    if wer <= thresholds.fail_wer and cer <= thresholds.fail_cer:
        return "review"
    return "fail"


def verify_text(
    *,
    expected: str,
    transcript: str,
    thresholds: VerificationThresholds = _DEFAULT_THRESHOLDS,
) -> TextVerificationResult:
    normalized_expected = normalize_text(expected)
    normalized_transcript = normalize_text(transcript)
    words = edit_distance(normalized_expected.split(), normalized_transcript.split())
    chars = edit_distance(
        normalized_expected.replace(" ", ""), normalized_transcript.replace(" ", "")
    )
    return TextVerificationResult(
        expected=expected,
        transcript=transcript,
        normalized_expected=normalized_expected,
        normalized_transcript=normalized_transcript,
        wer=words.rate,
        cer=chars.rate,
        wer_distance=words.distance,
        wer_substitutions=words.substitutions,
        wer_deletions=words.deletions,
        wer_insertions=words.insertions,
        cer_distance=chars.distance,
        status=classify_verification(
            wer=words.rate,
            cer=chars.rate,
            transcript=transcript,
            thresholds=thresholds,
        ),
    )


__all__ = [
    "EditDistance",
    "TextVerificationResult",
    "VerificationStatus",
    "VerificationThresholds",
    "character_error_rate",
    "classify_verification",
    "edit_distance",
    "normalize_text",
    "verify_text",
    "word_error_rate",
]
