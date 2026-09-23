from __future__ import annotations

import pytest

from readio.config import ReaderSettings, ReadioConfig
from readio.document import document_from_text
from readio.project import init_project
from readio.selection import (
    ProjectUnitSelection,
    ScopedUnitSelection,
    SelectionError,
    resolve_project_selection,
    resolve_unit_selection,
)
from readio.stages.planning import (
    load_primary_scope_plan,
    load_scope_plan,
    plan_project,
    plan_project_scope,
)


def _plan(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text(
        "One sentence. Two sentence.\n\nThree sentence. Four sentence.", encoding="utf-8"
    )
    project = init_project(source, tmp_path / "book.readio")
    plan_project(project, ReadioConfig(reader=ReaderSettings(spacy="off")))
    return load_primary_scope_plan(project)


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


def _two_scope_plans(tmp_path):
    source = tmp_path / "multi.txt"
    source.write_text("Project scope placeholder.", encoding="utf-8")
    project = init_project(source, tmp_path / "multi.readio")
    cfg = ReadioConfig(reader=ReaderSettings(spacy="off"))
    plan_project_scope(
        project,
        cfg,
        "chapter-0002",
        document_from_text("Alpha one. Alpha two.\n\nBeta one. Beta two."),
        title="Chapter Two",
    )
    plan_project_scope(
        project,
        cfg,
        "chapter-0003",
        document_from_text("Gamma one. Gamma two.\n\nDelta one. Delta two."),
        title="Chapter Three",
    )
    scopes = project.load_plan_index().scopes
    return project, tuple((scope, load_scope_plan(project, scope)) for scope in scopes)


def test_project_selection_flattens_units_across_ordered_scopes(tmp_path):
    _, plan_scopes = _two_scope_plans(tmp_path)

    first = resolve_project_selection(plan_scopes, "first:5")
    assert isinstance(first, ProjectUnitSelection)
    assert [scope.unit_indices for scope in first.scopes] == [(0, 1, 2, 3), (0,)]
    assert sum(len(scope.unit_indices) for scope in first.scopes) == 5

    ranged = resolve_project_selection(plan_scopes, "unit:4-5")
    assert [scope.unit_indices for scope in ranged.scopes] == [(3,), (0,)]

    paragraph = resolve_project_selection(plan_scopes, "paragraph:3")
    assert [scope.unit_indices for scope in paragraph.scopes] == [(), (0, 1)]

    sentence = resolve_project_selection(plan_scopes, "sentence:5")
    assert [scope.unit_indices for scope in sentence.scopes] == [(), (0,)]

    last_paragraph = resolve_project_selection(plan_scopes, "last-paragraph")
    assert [scope.unit_indices for scope in last_paragraph.scopes] == [(), (2, 3)]

    all_units = resolve_project_selection(plan_scopes, "all")
    assert [scope.unit_indices for scope in all_units.scopes] == [(0, 1, 2, 3), (0, 1, 2, 3)]


def test_project_selection_delegates_single_scope_and_checks_ranges(tmp_path):
    _, plan_scopes = _two_scope_plans(tmp_path)
    plan = plan_scopes[0][1]
    expected = resolve_unit_selection(plan, "paragraph:1")
    actual = resolve_project_selection(plan_scopes[:1], "paragraph:1")

    assert actual.scopes == (
        ScopedUnitSelection(
            scope_id="chapter-0002",
            unit_indices=expected.unit_indices,
            segment_ids=expected.segment_ids,
        ),
    )
    with pytest.raises(SelectionError, match="project has 8 units"):
        resolve_project_selection(plan_scopes, "unit:8-9")
