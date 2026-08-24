from dataclasses import dataclass

import pytest

from readio.reader import SelectionError, _selected_indices


@dataclass
class Unit:
    index: int


class Prepared:
    def __init__(self, n: int):
        self.units = tuple(Unit(i) for i in range(n))


def test_last_paragraph():
    assert _selected_indices(Prepared(3), "last-paragraph") == (2,)


def test_specific_paragraph_is_one_based():
    assert _selected_indices(Prepared(3), "paragraph:2") == (1,)


def test_out_of_range_paragraph():
    with pytest.raises(SelectionError):
        _selected_indices(Prepared(2), "paragraph:3")
