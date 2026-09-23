from __future__ import annotations

import pytest

from readio.chapter_selection import parse_chapter_selection


def test_all_and_single_number() -> None:
    assert parse_chapter_selection(None, 4) == (0, 1, 2, 3)
    assert parse_chapter_selection("all", 3) == (0, 1, 2)
    assert parse_chapter_selection("3", 4) == (2,)


def test_ranges_remove_duplicates_and_keep_source_order() -> None:
    assert parse_chapter_selection("1-5", 6) == (0, 1, 2, 3, 4)
    assert parse_chapter_selection("1-3,7,9-10", 10) == (0, 1, 2, 6, 8, 9)
    assert parse_chapter_selection("2-4,5", 6) == (1, 2, 3, 4)
    assert parse_chapter_selection("5, 2-3, 3", 6) == (1, 2, 4)
    assert parse_chapter_selection(" 2 - 4 , 5 ", 6) == (1, 2, 3, 4)


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("", "chapter selection is empty"),
        ("0", "chapter numbers must be positive"),
        ("-1", "invalid chapter selection"),
        ("5-2", "range start must be <= end"),
        ("7", "chapter 7 is out of range; EPUB has 6 chapters"),
        ("all,2", "'all' must be used alone"),
        ("1,,2", "chapter selection is empty"),
    ],
)
def test_invalid_selection(spec: str, message: str) -> None:
    with pytest.raises(ValueError, match=message.replace("'", "\\'")):
        parse_chapter_selection(spec, 6)


def test_empty_book_has_no_selectable_chapters() -> None:
    with pytest.raises(ValueError, match="no chapters"):
        parse_chapter_selection("all", 0)
