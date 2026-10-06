from __future__ import annotations

import json

import pytest

from readio.config import default_config
from readio.project import find_project, init_project, load_project
from readio.stages.planning import plan_project


def test_init_creates_project_layout_and_is_suffix_independent(tmp_path):
    source = tmp_path / "book.md"
    source.write_text("# Heading\n\nA sentence.", encoding="utf-8")
    root = tmp_path / "book-folder"
    project = init_project(source, root)
    assert (root / "project.json").is_file()
    assert (root / "source" / "book.md").is_file()
    assert (root / "document" / "document.ssmd.md").is_file()
    assert load_project(root).root == root.resolve()
    assert project.manifest.source_path == "source/book.md"

    assert project.manifest.schema_version == 3
    assert project.document_scopes()[0].path == "document/document.ssmd.md"
    assert (root / "document" / "index.json").is_file()


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
    plan = UtterancePlan.load(project.root / "plan" / "document.utterplan.toml")
    assert plan.plan_id
    assert project.load_plan_index().scopes[0].path == "document.utterplan.toml"


def test_attached_project_keeps_workspace_and_state_paths_separate(tmp_path):
    from readio.project_model import DocumentIndex, DocumentScope, ProjectManifest

    workspace = tmp_path / "book.ssmdbook"
    state = workspace / ".readio"
    chapter = workspace / "chapters" / "chapter-0001.ssmd.md"
    workspace.mkdir()
    workspace.joinpath("manifest.json").write_text("{}", encoding="utf-8")
    chapter.parent.mkdir(parents=True)
    chapter.write_text("Canonical chapter text.", encoding="utf-8")
    for directory in (state / "document", state / "plan", state / "synthesis"):
        directory.mkdir(parents=True)
    manifest = ProjectManifest(
        project_id="sha256:attached-project",
        name="Book",
        source_path="manifest.json",
        source_format="ssmdbook",
        source_sha256="a" * 64,
        kind="audiobook",
        schema_version=4,
        layout_mode="attached-ssmdbook",
        workspace_manifest_path="manifest.json",
        workspace_manifest_sha256="a" * 64,
    )
    (state / "project.json").write_text(json.dumps(manifest.to_dict()), encoding="utf-8")
    index = DocumentIndex(
        scopes=(
            DocumentScope(
                id="chapter-0001",
                kind="chapter",
                path="chapters/chapter-0001.ssmd.md",
                input_format="ssmd",
                source_number=1,
            ),
        ),
    )
    (state / "document" / "index.json").write_text(json.dumps(index.to_dict()), encoding="utf-8")

    project = load_project(workspace / "chapters")
    assert project.root == workspace
    assert project.workspace_root == workspace
    assert project.state_root == state
    assert project.paths["project"] == state / "project.json"
    assert project.paths["source"] == workspace / "manifest.json"
    assert project.paths["lock"] == state / ".lock"
    assert (
        project.load_document_scope(project.document_scopes()[0]).text == "Canonical chapter text."
    )
    assert load_project(state / "plan").root == workspace
    for candidate in (
        workspace,
        chapter,
        state,
        state / "plan",
        state / "document",
        state / "project.json",
    ):
        assert find_project(candidate) == workspace
    with pytest.raises(ValueError, match="contained"):
        project.workspace_path("../outside.ssmd.md")
    with pytest.raises(ValueError, match="contained"):
        project.state_path("../outside.json")


def test_find_project_prefers_nearest_attached_workspace(tmp_path):
    outer = tmp_path / "outer.ssmdbook"
    inner = outer / "chapters" / "nested.ssmdbook"
    (outer / ".readio").mkdir(parents=True)
    (outer / ".readio" / "project.json").write_text("{}", encoding="utf-8")
    (inner / ".readio").mkdir(parents=True)
    (inner / ".readio" / "project.json").write_text("{}", encoding="utf-8")

    assert find_project(inner / ".readio" / "plan") == inner
    assert find_project(outer / "chapters" / "unattached") == outer


def test_attached_project_rejects_symlinked_state_root(tmp_path):
    from readio.project_model import ProjectManifest

    workspace = tmp_path / "book.ssmdbook"
    outside_state = tmp_path / "external-state"
    workspace.mkdir()
    outside_state.mkdir()
    (workspace / "manifest.json").write_text("{}", encoding="utf-8")
    (outside_state / "project.json").write_text(
        json.dumps(
            ProjectManifest(
                project_id="sha256:attached-project",
                name="Book",
                source_path="manifest.json",
                source_format="ssmdbook",
                source_sha256="a" * 64,
                kind="audiobook",
                schema_version=4,
                layout_mode="attached-ssmdbook",
                workspace_manifest_path="manifest.json",
                workspace_manifest_sha256="a" * 64,
            ).to_dict()
        ),
        encoding="utf-8",
    )
    (workspace / ".readio").symlink_to(outside_state, target_is_directory=True)

    with pytest.raises(ValueError, match="must not be a symlink"):
        load_project(workspace)
