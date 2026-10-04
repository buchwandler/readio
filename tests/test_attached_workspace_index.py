from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from audiobook_support import make_epub
from ssmdconvert import refresh_book_workspace

from readio.audiobook import refresh_audiobook_index
from readio.integrations.ssmdconvert import (
    convert_book_source,
    load_book_workspace_source,
    write_book_bundle,
)
from readio.project import ProjectError, hash_file, load_project
from readio.project_model import DocumentIndex, DocumentScope, ProjectManifest
from readio.stages.planning import semantic_status


def _create_attached_project(workspace: Path, *, selection: tuple[int, ...]):
    state = workspace / ".readio"
    (state / "document").mkdir(parents=True)
    (state / "plan").mkdir()
    (state / "synthesis").mkdir()
    workspace_book = load_book_workspace_source(workspace)
    chapters = {chapter.source_number: chapter for chapter in workspace_book.chapters}
    scopes = tuple(
        DocumentScope(
            id=chapter.id,
            kind="chapter",
            path=workspace_book.chapter_paths[chapter.id],
            input_format="ssmd",
            title=chapter.title,
            source_number=chapter.source_number,
            extracted_sha256=hash_file(workspace / workspace_book.chapter_paths[chapter.id]),
        )
        for number in selection
        for chapter in (chapters[number],)
    )
    manifest_path = workspace / "manifest.json"
    manifest_sha = hash_file(manifest_path)
    manifest = ProjectManifest(
        project_id="sha256:stable-attached-book",
        name=workspace.stem,
        source_path="manifest.json",
        source_format="ssmdbook",
        source_sha256=manifest_sha,
        kind="audiobook",
        schema_version=4,
        layout_mode="attached-ssmdbook",
        workspace_manifest_path="manifest.json",
        workspace_manifest_sha256=manifest_sha,
    )
    (state / "project.json").write_text(json.dumps(manifest.to_dict()), encoding="utf-8")
    index = DocumentIndex(scopes=scopes, metadata={"stale": True}, selection=selection)
    (state / "document" / "index.json").write_text(json.dumps(index.to_dict()), encoding="utf-8")
    return load_project(workspace)


def test_attached_index_refresh_uses_current_bytes_preserves_selection_and_only_writes_state(
    tmp_path: Path,
) -> None:
    epub = tmp_path / "novel.epub"
    make_epub(epub)
    workspace = tmp_path / "novel.ssmdbook"
    write_book_bundle(convert_book_source(epub), workspace, format="directory")
    project = _create_attached_project(workspace, selection=(2, 3))
    initial_index_bytes = project.paths["document_index"].read_bytes()

    initial_book = load_book_workspace_source(workspace)
    selected_chapter = next(
        chapter for chapter in initial_book.chapters if chapter.source_number == 2
    )
    chapter_path = workspace / initial_book.chapter_paths[selected_chapter.id]
    chapter_path.write_text(
        chapter_path.read_text(encoding="utf-8") + "\n\nEdited canonical chapter.\n",
        encoding="utf-8",
    )
    canonical_manifest_bytes = (workspace / "manifest.json").read_bytes()
    project_manifest_bytes = project.paths["project"].read_bytes()
    original_chapter_bytes = chapter_path.read_bytes()

    refreshed_book = refresh_audiobook_index(project)
    refreshed_index = project.load_document_index()
    refreshed_scope = next(scope for scope in refreshed_index.scopes if scope.source_number == 2)
    rows = semantic_status(project)

    assert refreshed_book.workspace_dirty is True
    assert refreshed_index.selection == (2, 3)
    assert tuple(scope.source_number for scope in refreshed_index.scopes) == (2, 3)
    assert refreshed_scope.path == initial_book.chapter_paths[selected_chapter.id]
    assert refreshed_scope.extracted_sha256 == hash_file(chapter_path)
    assert project.load_document_scope(refreshed_scope).text.endswith("Edited canonical chapter.\n")
    refreshed_document = project.load_document_scope(refreshed_scope)
    assert refreshed_document.provenance is not None
    assert refreshed_document.provenance.metadata["language"] == refreshed_book.metadata["language"]
    assert project.paths["document_index"].read_bytes() != initial_index_bytes
    assert project.paths["project"].read_bytes() == project_manifest_bytes
    assert (workspace / "manifest.json").read_bytes() == canonical_manifest_bytes
    assert chapter_path.read_bytes() == original_chapter_bytes
    assert project.paths["document_index"].is_relative_to(project.state_root)
    assert rows[1]["state"] == "current"
    assert rows[1]["workspace"]["status"] == "dirty"
    assert rows[1]["workspace"]["dirty_chapter_count"] == 1


def test_attached_index_refresh_reports_disappeared_selected_chapter_id(tmp_path: Path) -> None:
    epub = tmp_path / "novel.epub"
    make_epub(epub)
    workspace = tmp_path / "novel.ssmdbook"
    write_book_bundle(convert_book_source(epub), workspace, format="directory")
    project = _create_attached_project(workspace, selection=(2, 3))
    index = project.load_document_index()
    stale_scope = replace(index.scopes[0], id="removed-chapter-id")
    stale_index = DocumentIndex(
        scopes=(stale_scope, *index.scopes[1:]),
        metadata=index.metadata,
        selection=index.selection,
    )
    project.paths["document_index"].write_text(json.dumps(stale_index.to_dict()), encoding="utf-8")

    with pytest.raises(ProjectError, match="chapter ID.*no longer exist"):
        refresh_audiobook_index(project)


def test_manual_edit_invalidates_only_its_canonical_plan_input(tmp_path: Path) -> None:
    from readio.config import default_config
    from readio.stages.planning import plan_project

    epub = tmp_path / "novel.epub"
    make_epub(epub)
    workspace = tmp_path / "novel.ssmdbook"
    write_book_bundle(convert_book_source(epub), workspace, format="directory")
    project = _create_attached_project(workspace, selection=(2, 3))
    plan_project(project, default_config())
    before_index = project.load_document_index()
    before_plans = project.load_plan_index()
    before_hashes = {scope.source_number: scope.extracted_sha256 for scope in before_index.scopes}
    plan_hashes = {scope.id: scope.document_sha256 for scope in before_plans.scopes}
    project_manifest_bytes = project.paths["project"].read_bytes()
    workspace_book = load_book_workspace_source(workspace)
    changed_chapter = next(
        chapter for chapter in workspace_book.chapters if chapter.source_number == 2
    )
    changed_path = workspace / workspace_book.chapter_paths[changed_chapter.id]
    changed_path.write_text(
        changed_path.read_text(encoding="utf-8") + "\n\nA canonical edit.\n",
        encoding="utf-8",
    )

    rows = semantic_status(project)
    after_index = project.load_document_index()
    after_hashes = {scope.source_number: scope.extracted_sha256 for scope in after_index.scopes}
    changed_scope = next(scope for scope in after_index.scopes if scope.source_number == 2)
    unchanged_scope = next(scope for scope in after_index.scopes if scope.source_number == 3)

    assert rows[1]["state"] == "current"
    assert rows[2]["state"] == "stale"
    assert rows[2]["reason"] == "plan.stale.document_changed"
    assert rows[2]["scope_id"] == changed_chapter.id
    assert after_hashes[2] == hash_file(changed_path)
    assert after_hashes[2] != before_hashes[2]
    assert after_hashes[3] == before_hashes[3]
    assert plan_hashes[changed_chapter.id] != changed_scope.extracted_sha256
    assert plan_hashes[unchanged_scope.id] == unchanged_scope.extracted_sha256
    assert project.load_document_scope(changed_scope).text.endswith("A canonical edit.\n")
    assert rows[1]["workspace"]["status"] == "dirty"

    refreshed_workspace = refresh_book_workspace(workspace)
    clean_rows = semantic_status(project)
    assert refreshed_workspace.dirty is False
    assert clean_rows[1]["workspace"]["status"] == "clean"
    assert project.paths["project"].read_bytes() == project_manifest_bytes
    assert project.state_root.is_dir()
