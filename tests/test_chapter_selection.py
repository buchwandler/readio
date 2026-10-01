from __future__ import annotations

import pytest

from readio.chapter_selection import parse_chapter_selection

_AVAILABLE = (2, 3, 4, 7)


def test_all_and_single_number() -> None:
    assert parse_chapter_selection(None, available_numbers=_AVAILABLE) == _AVAILABLE
    assert parse_chapter_selection("all", available_numbers=_AVAILABLE) == _AVAILABLE
    assert parse_chapter_selection("3", available_numbers=_AVAILABLE) == (3,)


def test_selection_uses_available_source_numbers_and_keeps_source_order() -> None:
    assert parse_chapter_selection("3-4", available_numbers=_AVAILABLE) == (3, 4)
    assert parse_chapter_selection("2-4,7", available_numbers=_AVAILABLE) == _AVAILABLE
    assert parse_chapter_selection("7, 3, 3", available_numbers=_AVAILABLE) == (3, 7)
    assert parse_chapter_selection(" 2 - 4 , 7 ", available_numbers=_AVAILABLE) == _AVAILABLE


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("", "chapter selection is empty"),
        ("0", "chapter numbers must be positive"),
        ("-1", "invalid chapter selection"),
        ("4-2", "range start must be <= end"),
        ("5", "chapter 5 is not available in this book source"),
        ("3-5", "chapter 5 is not available in this book source"),
        ("all,3", "'all' must be used alone"),
        ("2,,3", "chapter selection is empty"),
    ],
)
def test_invalid_selection(spec: str, message: str) -> None:
    with pytest.raises(ValueError, match=message.replace("'", "\\'")):
        parse_chapter_selection(spec, available_numbers=_AVAILABLE)


def test_empty_book_has_no_selectable_chapters() -> None:
    with pytest.raises(ValueError, match="no chapters"):
        parse_chapter_selection("all", available_numbers=())
