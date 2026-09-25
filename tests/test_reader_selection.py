from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from readio.selection import SelectionError, resolve_unit_selection


@dataclass
class Unit:
    index: int
    segment_ids: tuple[str, ...]
    kind: str = "paragraph"


def _plan(paragraphs: tuple[int, ...]):
    segments = tuple(
        SimpleNamespace(id=f"segment-{index}", paragraph=paragraph, sentence=index)
        for index, paragraph in enumerate(paragraphs)
    )
    units = tuple(
        Unit(index=index, segment_ids=(segment.id,)) for index, segment in enumerate(segments)
    )
    return SimpleNamespace(units=units, segments=segments)


def test_last_paragraph_selects_its_plan_units():
    selection = resolve_unit_selection(_plan((0, 1, 1)), "last-paragraph")

    assert selection.unit_indices == (1, 2)
    assert selection.segment_ids == ("segment-1", "segment-2")


def test_specific_paragraph_is_one_based():
    selection = resolve_unit_selection(_plan((0, 1, 1)), "paragraph:2")

    assert selection.unit_indices == (1, 2)


def test_out_of_range_paragraph():
    with pytest.raises(SelectionError):
        resolve_unit_selection(_plan((0, 1)), "paragraph:3")
