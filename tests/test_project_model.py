from __future__ import annotations

import json
from dataclasses import replace

import pytest

from readio.project import init_project, load_project, update_project_manifest
from readio.project_model import (
    DocumentIndex,
    DocumentScope,
    ProjectFormatError,
    ProjectManifest,
)
from readio.project_settings import (
    project_ssmd_settings,
    project_voice_bindings,
    with_project_voice_binding,
    without_project_voice_binding,
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
        DocumentIndex.from_dict(DocumentIndex(scopes=(scope, scope)).to_dict())


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


@pytest.mark.parametrize(
    "settings",
    [
        [],
        {"ssmd": None},
        {"ssmd": []},
        {"ssmd": {"voice_bindings": []}},
        {"ssmd": {"voice_bindings": {"": {}}}},
        {"ssmd": {"voice_bindings": {"kokoro": []}}},
        {"ssmd": {"voice_bindings": {"kokoro": {"": "af_heart"}}}},
        {"ssmd": {"voice_bindings": {"kokoro": {"narrator": ""}}}},
    ],
)
def test_project_manifest_rejects_malformed_voice_binding_settings(tmp_path, settings) -> None:
    source = tmp_path / "book.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    payload = project.manifest.to_dict()
    payload["settings"] = settings

    with pytest.raises(ProjectFormatError):
        ProjectManifest.from_dict(payload)


@pytest.mark.parametrize("provider", ["piper", "kokoro"])
def test_project_manifest_accepts_ssmd_voice_provider(tmp_path, provider: str) -> None:
    source = tmp_path / "book.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    payload = project.manifest.to_dict()
    payload["settings"] = {"ssmd": {"voice_provider": provider}}
    manifest = ProjectManifest.from_dict(payload)

    assert manifest.schema_version == 2
    assert manifest.settings["ssmd"]["voice_provider"] == provider


@pytest.mark.parametrize("provider", ["", 1, None])
def test_project_manifest_rejects_invalid_ssmd_voice_provider(tmp_path, provider) -> None:
    source = tmp_path / "book.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    payload = project.manifest.to_dict()
    payload["settings"] = {"ssmd": {"voice_provider": provider}}

    with pytest.raises(ProjectFormatError, match="voice_provider must be a non-empty string"):
        ProjectManifest.from_dict(payload)


def test_project_voice_binding_helpers_preserve_unrelated_settings_and_providers(
    tmp_path,
) -> None:
    source = tmp_path / "book.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    original_settings = {
        "custom": {"keep": True},
        "ssmd": {
            "custom": "preserved",
            "voice_bindings": {
                "kokoro": {"host": "af_sarah"},
                "piper": {"narrator": "en_US-lessac-medium"},
            },
        },
    }
    manifest = replace(project.manifest, settings=original_settings)

    changed = with_project_voice_binding(
        manifest, provider="kokoro", role="narrator", voice="af_heart"
    )
    assert manifest.settings == original_settings
    assert project_voice_bindings(changed, "kokoro") == {
        "host": "af_sarah",
        "narrator": "af_heart",
    }
    assert project_voice_bindings(changed, "piper") == {"narrator": "en_US-lessac-medium"}
    ssmd = project_ssmd_settings(changed)
    ssmd["custom"] = "changed copy"
    assert changed.settings["ssmd"]["custom"] == "preserved"

    unbound = without_project_voice_binding(changed, provider="kokoro", role="narrator")
    assert project_voice_bindings(unbound, "kokoro") == {"host": "af_sarah"}
    assert project_voice_bindings(unbound, "piper") == {"narrator": "en_US-lessac-medium"}
    assert unbound.settings["custom"] == {"keep": True}
    assert unbound.settings["ssmd"]["custom"] == "preserved"


def test_update_project_manifest_persists_binding_atomically_under_lock(tmp_path) -> None:
    source = tmp_path / "book.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")

    updated = update_project_manifest(
        project,
        lambda manifest: with_project_voice_binding(
            manifest, provider="kokoro", role="narrator", voice="af_heart"
        ),
        operation="bind-voice",
    )

    assert project_voice_bindings(updated.manifest, "kokoro") == {"narrator": "af_heart"}
    assert not (project.root / ".lock").exists()
    persisted = json.loads((project.root / "project.json").read_text(encoding="utf-8"))
    assert persisted["settings"]["ssmd"]["voice_bindings"]["kokoro"]["narrator"] == "af_heart"
