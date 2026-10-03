"""Table-driven tests for Readio-owned metadata normalization (plan section 8.2)."""

from __future__ import annotations

import pytest

from readio.catalog_metadata import (
    language_base,
    language_tags_match,
    normalize_gender,
    normalize_locale_tag,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("en_US", "en-us"),
        ("en-US", "en-us"),
        ("en", "en"),
        ("de_DE", "de-de"),
        ({"code": "en_US"}, "en-us"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_locale_tag(raw: object, expected: str) -> None:
    assert normalize_locale_tag(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("en-us", "en"),
        ("de", "de"),
        ("de-DE", "de"),
    ],
)
def test_language_base(raw: str, expected: str) -> None:
    assert language_base(raw) == expected


@pytest.mark.parametrize(
    ("requested", "available", "matches"),
    [
        ("en", "en-us", True),
        ("en-us", "en_US", True),
        ("de", "en-us", False),
        ("en-us", "en-gb", False),
        ("en", "en", True),
    ],
)
def test_language_tags_match(requested: str, available: str, matches: bool) -> None:
    assert language_tags_match(requested, available) is matches


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("female", "female"),
        ("MALE", "male"),
        ("neutral", "neutral"),
        ("unknown", "unknown"),
        (None, "unknown"),
        ("n/a", "unknown"),
    ],
)
def test_normalize_gender(raw: object, expected: str) -> None:
    assert normalize_gender(raw) == expected
