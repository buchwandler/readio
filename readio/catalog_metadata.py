"""Readio-owned neutral metadata policy for locale tags, languages, and gender.

This is Readio metadata semantics, not model-runtime behavior. Engine adapters
canonicalize their descriptive metadata with these helpers; generic layers only
project and filter the resulting values.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .config import normalize_language_key

GENDERS = frozenset({"female", "male", "neutral", "unknown"})


def normalize_locale_tag(value: Any) -> str:
    """Return the canonical Readio locale tag: lowercase, hyphen-separated.

    Accepts strings and legacy language mappings such as ``{"code": "en_US"}``.
    """
    if isinstance(value, Mapping):
        value = value.get("code", "")
    if not isinstance(value, str) or not value.strip():
        return ""
    return normalize_language_key(value)


def language_base(value: Any) -> str:
    """Return the primary language subtag of a locale tag, or an empty string."""
    return normalize_locale_tag(value).partition("-")[0]


def language_tags_match(requested: str, available: str) -> bool:
    """Return whether an available language tag satisfies a request.

    A generic tag matches any specific tag with the same base language. Two
    different specific tags do not match.
    """
    requested_tag = normalize_locale_tag(requested)
    available_tag = normalize_locale_tag(available)
    if not requested_tag or not available_tag:
        return False
    requested_base = language_base(requested_tag)
    available_base = language_base(available_tag)
    return requested_base == available_base and (
        requested_tag in (available_tag, requested_base) or available_tag == available_base
    )


def normalize_gender(value: Any) -> str:
    """Normalize gender metadata to Readio's public vocabulary.

    Never infers gender from names, IDs, or other heuristics.
    """
    if isinstance(value, str) and value.strip().lower() in GENDERS:
        return value.strip().lower()
    return "unknown"


__all__ = [
    "GENDERS",
    "language_base",
    "language_tags_match",
    "normalize_gender",
    "normalize_locale_tag",
]
