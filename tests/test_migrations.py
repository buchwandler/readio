from __future__ import annotations

import json
from pathlib import Path

import pytest

from readio.migrations import MigrationError, migrate_project
from readio.project import init_project, load_project


def _write_manifest(project, payload: dict[str, object]) -> None:
    (project.root / "project.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_project_v03_migration_backups_state_converts_roles_and_invalidates_stages(
    tmp_path: Path,
) -> None:
    source = tmp_path / "book.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    payload = project.manifest.to_dict()
    payload["schema_version"] = 2
    payload["settings"] = {
        "synthesis": {"engine": "pykokoro"},
        "ssmd": {
            "voice_provider": "pykokoro",
            "voice_bindings": {
                "pykokoro": {"narrator": "af_heart"},
                "piper": {"guest": "en_US-amy-medium"},
            },
        },
    }
    _write_manifest(project, payload)
    state_paths = (
        Path("synthesis/profile.json"),
        Path("synthesis/trace.json"),
        Path("composition/state.json"),
        Path("output/state.json"),
    )
    for relative in state_paths:
        state = project.root / relative
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("{}", encoding="utf-8")

    backup = migrate_project(project.root)
    assert backup is not None
    assert json.loads((backup / "project.json").read_text(encoding="utf-8"))["schema_version"] == 2
    assert all((backup / relative).is_file() for relative in state_paths)
    assert all(not (project.root / relative).exists() for relative in state_paths)

    migrated = load_project(project.root)
    assert migrated.manifest.schema_version == 3
    settings = migrated.manifest.settings
    assert settings["synthesis"]["engine"] == "kokoro"
    assert "voice_provider" not in settings["ssmd"]
    assert "voice_bindings" not in settings["ssmd"]
    assert settings["ssmd"]["role_bindings"] == {
        "guest": {"engine": "piper", "voice": "en_US-amy-medium"},
        "narrator": {"engine": "kokoro", "voice": "af_heart"},
    }
    assert migrate_project(project.root) is None


def test_schema_one_project_migration_materializes_document_index(tmp_path: Path) -> None:
    source = tmp_path / "book.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    old_index = (project.root / "document/index.json").read_bytes()
    text_path = project.root / "document/document.txt"
    text_path.write_text("Hello from v0.3.", encoding="utf-8")
    metadata_path = project.root / "document/metadata.json"
    metadata_path.write_text(
        json.dumps({"document_format": "text", "document_sha256": "abc123"}),
        encoding="utf-8",
    )
    payload = project.manifest.to_dict()
    payload["schema_version"] = 1
    payload.pop("kind")
    payload["document"] = {
        "metadata_path": "document/metadata.json",
        "text_path": "document/document.txt",
    }
    _write_manifest(project, payload)

    backup = migrate_project(project.root)
    assert backup is not None
    assert (backup / "document/index.json").read_bytes() == old_index
    migrated = load_project(project.root)
    scope = migrated.document_scopes()[0]
    assert scope.path == "document/document.txt"
    assert scope.input_format == "text"
    assert scope.extracted_sha256 == "abc123"
    assert migrated.document().text == "Hello from v0.3."
    assert (project.root / "document/index.json").is_file()


def test_project_migration_rolls_back_index_if_manifest_commit_fails(
    tmp_path: Path, monkeypatch
) -> None:
    import readio.migrations.v03_to_v04 as migration_module

    source = tmp_path / "book.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    old_index = (project.root / "document/index.json").read_bytes()
    text_path = project.root / "document/document.txt"
    text_path.write_text("Legacy text.", encoding="utf-8")
    metadata_path = project.root / "document/metadata.json"
    metadata_path.write_text(json.dumps({"document_format": "text"}), encoding="utf-8")
    payload = project.manifest.to_dict()
    payload["schema_version"] = 1
    payload.pop("kind")
    payload["document"] = {
        "metadata_path": "document/metadata.json",
        "text_path": "document/document.txt",
    }
    _write_manifest(project, payload)
    manifest_path = project.root / "project.json"
    original_manifest = manifest_path.read_bytes()
    state_path = project.root / "synthesis/profile.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("old profile", encoding="utf-8")

    atomic_write_json = migration_module.atomic_write_json
    failed_once = False

    def fail_first_manifest_commit(path, data):
        nonlocal failed_once
        if Path(path) == manifest_path and not failed_once:
            failed_once = True
            raise OSError("simulated manifest commit failure")
        atomic_write_json(path, data)

    monkeypatch.setattr(migration_module, "atomic_write_json", fail_first_manifest_commit)
    with pytest.raises(MigrationError, match="original data was restored"):
        migrate_project(project.root)

    assert manifest_path.read_bytes() == original_manifest
    assert (project.root / "document/index.json").read_bytes() == old_index
    assert state_path.read_text(encoding="utf-8") == "old profile"
    backups = tuple(project.root.glob(".readio-v03-backup-*"))
    assert len(backups) == 1
    assert (backups[0] / "project.json").read_bytes() == original_manifest
    assert (backups[0] / "document/index.json").read_bytes() == old_index


def test_project_migration_refuses_an_existing_lock(tmp_path: Path) -> None:
    source = tmp_path / "book.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    payload = project.manifest.to_dict()
    payload["schema_version"] = 2
    _write_manifest(project, payload)
    original = (project.root / "project.json").read_bytes()
    lock = project.root / ".lock"
    lock.write_text("another process", encoding="utf-8")

    with pytest.raises(MigrationError, match="locked for another mutation"):
        migrate_project(project.root)

    assert (project.root / "project.json").read_bytes() == original
    assert lock.read_text(encoding="utf-8") == "another process"


def test_project_migration_rejects_path_traversal_without_replacing_manifest(
    tmp_path: Path,
) -> None:
    source = tmp_path / "book.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    payload = project.manifest.to_dict()
    payload["schema_version"] = 2
    payload["source"]["path"] = "../outside.txt"
    _write_manifest(project, payload)
    original = (project.root / "project.json").read_bytes()

    with pytest.raises(MigrationError, match="escapes the project root"):
        migrate_project(project.root)
    assert (project.root / "project.json").read_bytes() == original
    assert not (project.root / ".lock").exists()
