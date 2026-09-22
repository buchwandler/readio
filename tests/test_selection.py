from __future__ import annotations

from readio.config import ReaderSettings, ReadioConfig
from readio.project import init_project
from readio.selection import resolve_unit_selection
from readio.stages.planning import load_scope_plan, plan_project


def _plan(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text(
        "One sentence. Two sentence.\n\nThree sentence. Four sentence.", encoding="utf-8"
    )
    project = init_project(source, tmp_path / "book.readio")
    plan_project(project, ReadioConfig(reader=ReaderSettings(spacy="off")))
    return load_scope_plan(project)


def test_selectors_cover_ranges_and_paragraphs_over_sentence_units(tmp_path):
    plan = _plan(tmp_path)
    assert resolve_unit_selection(plan, "all").unit_indices == tuple(
        unit.index for unit in plan.units
    )
    assert len(resolve_unit_selection(plan, "first:1").unit_indices) == 1
    assert len(resolve_unit_selection(plan, "last:1").unit_indices) == 1
    assert resolve_unit_selection(plan, "paragraph:1").unit_indices == (0, 1)
    assert resolve_unit_selection(plan, "paragraph:1-2").unit_indices == tuple(
        unit.index for unit in plan.units
    )
    assert resolve_unit_selection(plan, "sentence:1").unit_indices == (0,)
    assert resolve_unit_selection(plan, "unit:1-2").unit_indices == (0, 1)
    assert resolve_unit_selection(plan, "last-paragraph").unit_indices
