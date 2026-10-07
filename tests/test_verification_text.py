from __future__ import annotations

import pytest

from readio.verification.cases import (
    POCKET_SHORT_TAIL_CASE,
    READIO_E2E_CASE,
    READIO_TIMESTAMPS_CASE,
    get_case,
    list_cases,
)
from readio.verification.text import (
    VerificationThresholds,
    character_error_rate,
    classify_verification,
    edit_distance,
    normalize_text,
    verify_text,
    word_error_rate,
)


def test_normalization_metrics_and_error_breakdown() -> None:
    assert normalize_text("  ＣＬＥＡＲ—It’s  GOOD!\n") == "clear it's good"
    assert word_error_rate("one two three", "one three") == pytest.approx(1 / 3)
    assert character_error_rate("cat", "cut") == pytest.approx(1 / 3)
    assert edit_distance(["one", "two"], ["one", "new", "two"]).insertions == 1
    result = verify_text(expected="One two three.", transcript="one three")
    assert result.normalized_expected == "one two three"
    assert result.wer_deletions == 1
    assert result.status == "fail"


def test_threshold_validation_and_empty_transcript() -> None:
    with pytest.raises(ValueError, match="pass thresholds"):
        VerificationThresholds(pass_wer=0.3)
    assert classify_verification(wer=0, cer=0, transcript="!!!") == "fail"
    assert word_error_rate("", "extra words") == 1.0


def test_versioned_cases_keep_pocket_alias_and_order() -> None:
    assert [case.id for case in list_cases()] == [
        "readio-e2e-en-v1",
        "pocket-short-tail-v1",
        "readio-timestamps-en-v1",
    ]
    assert get_case("pocket-short-tail") == POCKET_SHORT_TAIL_CASE
    assert get_case("readio-timestamps-en-v1") == READIO_TIMESTAMPS_CASE
    assert get_case("default") == READIO_E2E_CASE
    assert POCKET_SHORT_TAIL_CASE.reference_text == "Hello, how are you?"
