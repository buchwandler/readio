from __future__ import annotations

import json

import pytest

from readio.project import init_project, load_project
from readio.project_model import (
    DocumentIndex,
    DocumentScope,
    ProjectFormatError,
    ProjectManifest,
)


def test_document_index_round_trips_scope_provenance_and_metadata() -> None:
    index = DocumentIndex(
        scopes=(
            DocumentScope(
                id="chapter-0002",
                kind="chapter",
                path="document/chapters/chapter-0002.md",
                input_format="markdown",
                title="Chapter One",
                source_number=2,
                source_id="nav:chapter-one",
                href="Text/chapter-one.xhtml",
                parent_id="part-two",
                level=2,
                char_count=42,
                extracted_sha256="abc123",
                diagnostics=({"code": "note", "message": "example"},),
            ),
        ),
        metadata={"title": "Book", "authors": ["Writer"]},
        selection=(2,),
    )

    assert DocumentIndex.from_dict(index.to_dict()) == index


def test_document_index_rejects_duplicate_scope_ids() -> None:
    scope = DocumentScope(
        id="chapter-0001",
        kind="chapter",
        path="document/chapters/chapter-0001.md",
        input_format="markdown",
    )
    with pytest.raises(ProjectFormatError, match="duplicate scope IDs"):
        DocumentIndex.from_dict(
            DocumentIndex(scopes=(scope, scope)).to_dict()
        )


def test_schema_one_manifest_and_project_are_read_as_one_document_scope(tmp_path) -> None:
    source = tmp_path / "book.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    manifest_payload = json.loads((project.root / "project.json").read_text(encoding="utf-8"))
    manifest_payload["schema_version"] = 1
    manifest_payload.pop("kind")
    manifest_payload["document"] = {
        "metadata_path": "document/metadata.json",
        "text_path": "document/document.txt",
    }
    (project.root / "project.json").write_text(json.dumps(manifest_payload), encoding="utf-8")

    loaded = load_project(project.root)
    index = loaded.load_document_index()

    assert loaded.manifest.schema_version == 1
    assert len(index.scopes) == 1
    assert index.scopes[0].id == "document"
    assert index.scopes[0].input_format == "text"
    assert loaded.document().text == "Hello."
    assert ProjectManifest.from_dict(manifest_payload).to_dict() == manifest_payload


def test_document_convenience_accessor_rejects_multiple_scopes(tmp_path) -> None:
    source = tmp_path / "book.txt"
    source.write_text("First.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    second_path = project.root / "document" / "second.txt"
    second_path.write_text("Second.", encoding="utf-8")
    index = DocumentIndex(
        scopes=(
            *project.document_scopes(),
            DocumentScope(
                id="chapter-0002",
                kind="chapter",
                path="document/second.txt",
                input_format="text",
            ),
        )
    )
    (project.root / "document" / "index.json").write_text(
        json.dumps(index.to_dict()), encoding="utf-8"
    )

    loaded = load_project(project.root)
    with pytest.raises(ValueError, match="multiple document scopes"):
        loaded.document()
    assert loaded.load_document_scope(index.scopes[1]).text == "Second."



def test_schema_two_manifest_points_to_document_index(tmp_path) -> None:
    source = tmp_path / "book.md"
    source.write_text("# Heading\n\nHello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    payload = json.loads((project.root / "project.json").read_text(encoding="utf-8"))

    assert payload["schema_version"] == 2
    assert payload["kind"] == "document"
    assert payload["document"] == {"index_path": "document/index.json"}
    assert ProjectManifest.from_dict(payload).to_dict() == payload
    assert project.document_scopes()[0].id == "document"
