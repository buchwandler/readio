from __future__ import annotations

import json

import pytest

from readio.config import default_config
from readio.project import init_project, load_project
from readio.stages.planning import plan_project


def test_init_creates_project_layout_and_is_suffix_independent(tmp_path):
    source = tmp_path / "book.md"
    source.write_text("# Heading\n\nA sentence.", encoding="utf-8")
    root = tmp_path / "book-folder"
    project = init_project(source, root)
    assert (root / "project.json").is_file()
    assert (root / "source" / "book.md").is_file()
    assert (root / "document" / "document.txt").is_file()
    assert load_project(root).root == root.resolve()
    assert project.manifest.source_path == "source/book.md"


def test_malformed_and_traversal_manifests_are_rejected(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("hello", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    payload = json.loads((project.root / "project.json").read_text(encoding="utf-8"))
    payload["source"]["path"] = "../outside.txt"
    (project.root / "project.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_project(project.root)


def test_plan_artifacts_are_valid_utterplan(tmp_path):
    from utterplan import UtterancePlan

    source = tmp_path / "book.txt"
    source.write_text("First sentence.\n\nSecond sentence.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    plan_project(project, default_config())
    plan = UtterancePlan.load(project.root / "plan" / "document.utterplan.json")
    assert plan.plan_id
    assert project.load_plan_index().scopes[0].path == "document.utterplan.json"
