from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from readio.project import init_project, load_project, update_project_manifest
from readio.project_model import (
    DocumentIndex,
    DocumentScope,
    PlanIndex,
    PlanScope,
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
                source_parent_id="source-nav:part-two",
                char_count=42,
                extracted_sha256="abc123",
                diagnostics=({"code": "note", "message": "example"},),
            ),
        ),
        metadata={"title": "Book", "authors": ["Writer"]},
        selection=(2,),
    )

    assert DocumentIndex.from_dict(index.to_dict()) == index


def test_plan_index_settings_fingerprint_is_optional_and_round_trips() -> None:
    scope = PlanScope(id="document", kind="document", path="document.utterplan.json")
    legacy = {
        "format": "readio.plan-index",
        "schema_version": 1,
        "scopes": [scope.to_dict()],
    }

    assert PlanIndex.from_dict(legacy).to_dict() == legacy

    configured = PlanIndex(
        scopes=(scope,),
        project_planning_settings_sha256=f"sha256:{'a' * 64}",
    )
    assert PlanIndex.from_dict(configured.to_dict()) == configured
    with pytest.raises(ProjectFormatError, match="project_planning_settings_sha256"):
        PlanIndex.from_dict({**legacy, "project_planning_settings_sha256": 42})


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
    legacy_document = project.root / "document" / "document.txt"
    legacy_document.write_text("Hello.", encoding="utf-8")
    legacy_metadata = project.root / "document" / "metadata.json"
    legacy_metadata.write_text(
        json.dumps(
            {
                "format": "readio.document",
                "schema_version": 1,
                "source_sha256": project.manifest.source_sha256,
                "input_format": "text",
                "document_format": "text",
            }
        ),
        encoding="utf-8",
    )
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


def test_project_pipeline_settings_round_trip_paths_and_preserve_unknown_namespaces(
    tmp_path,
) -> None:
    from readio.api.types import (
        AudiobookExportOptions,
        CompositionOptions,
        ExportOptions,
        ProjectSettings,
        ProjectSynthesisSettings,
    )
    from readio.project_settings import (
        project_settings_from_manifest,
        project_settings_to_dict,
        with_project_settings,
    )

    source = tmp_path / "book.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    settings = ProjectSettings(
        synthesis=ProjectSynthesisSettings(
            language="en-us",
            engine="pykokoro",
            speed=1.1,
            clear_lexicons=True,
            voice_file=Path("voices/reference.wav"),
            engine_options={"flags": [True, None], "length_scale": 1.2},
        ),
        composition=CompositionOptions(target_lufs=-18.0, sample_rate=48000),
        export=ExportOptions(
            format="flac", output=project.root / "output/book.flac", bitrate=None, force=True
        ),
        audiobook_export=AudiobookExportOptions(
            output=Path("output/book.m4b"),
            title="Book",
            author="Writer",
            cover=Path("cover.jpg"),
            bitrate="96k",
            force=True,
        ),
    )
    original_settings = {"custom": {"keep": True}, "ssmd": {"voice_provider": "kokoro"}}
    manifest = replace(project.manifest, settings=original_settings)

    changed = with_project_settings(manifest, settings, project.root)
    persisted = changed.to_dict()["settings"]
    restored = project_settings_from_manifest(changed, project.root)

    assert persisted["synthesis"]["voice_file"] == "voices/reference.wav"
    assert persisted["synthesis"]["engine_options"] == {
        "flags": [True, None],
        "length_scale": 1.2,
    }
    assert persisted["export"] == {
        "format": "flac",
        "output": "output/book.flac",
        "bitrate": None,
    }
    assert persisted["audiobook_export"]["cover"] == "cover.jpg"
    assert "force" not in persisted["export"]
    assert "force" not in persisted["audiobook_export"]
    assert restored.synthesis == replace(
        settings.synthesis, voice_file=project.root / "voices/reference.wav"
    )
    assert restored.composition == settings.composition
    assert restored.export == replace(settings.export, force=False)
    assert restored.audiobook_export == replace(
        settings.audiobook_export,
        output=project.root / "output/book.m4b",
        cover=project.root / "cover.jpg",
        force=False,
    )
    assert changed.settings["custom"] == {"keep": True}
    assert changed.settings["ssmd"] == {"voice_provider": "kokoro"}
    assert project_settings_to_dict(restored, project.root) == {
        key: value
        for key, value in persisted.items()
        if key in {"synthesis", "composition", "export", "audiobook_export"}
    }


@pytest.mark.parametrize(
    "settings",
    [
        {"synthesis": {"speed": float("nan")}},
        {"synthesis": {"spacy": "sometimes"}},
        {"synthesis": {"lexicons": "crane"}},
        {"synthesis": {"clear_lexicons": True, "auto_lexicons": True}},
        {"synthesis": {"refresh": False}},
        {"synthesis": {"engine_options": {"temperature": float("inf")}}},
        {"composition": {"sample_rate": True}},
        {"composition": {"peak_policy": []}},
        {"export": {"format": "m4b"}},
        {"export": {"force": False}},
        {"audiobook_export": {"format": "wav"}},
        {"audiobook_export": {"force": False}},
    ],
)
def test_project_manifest_rejects_malformed_pipeline_settings(tmp_path, settings) -> None:
    source = tmp_path / "book.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    payload = project.manifest.to_dict()
    payload["settings"] = settings

    with pytest.raises(ProjectFormatError):
        ProjectManifest.from_dict(payload)


def test_project_settings_patch_and_synthesis_request_merge(tmp_path) -> None:
    from readio.api.types import (
        UNSET,
        CompositionOptions,
        ExportOptions,
        ProjectSettings,
        ProjectSettingsPatch,
        ProjectSynthesisSettings,
    )
    from readio.plan import SynthesisRequest
    from readio.project_settings import (
        apply_project_settings_patch,
        merge_project_synthesis_request,
        project_settings_from_manifest,
        with_project_settings,
    )

    source = tmp_path / "book.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    settings = ProjectSettings(
        synthesis=ProjectSynthesisSettings(
            language="de",
            engine="piper",
            speed=1.2,
            auto_lexicons=True,
            allow_experimental=True,
            offline=True,
            engine_options={"length_scale": 1.1, "shared": "project"},
        ),
        composition=CompositionOptions(),
        export=ExportOptions(format="flac"),
    )
    manifest = with_project_settings(project.manifest, settings, project.root)
    patched = apply_project_settings_patch(
        manifest,
        ProjectSettingsPatch(synthesis=UNSET, composition=None),
        project.root,
    )
    patched_settings = project_settings_from_manifest(patched, project.root)

    assert patched_settings.synthesis == settings.synthesis
    assert patched_settings.composition is None
    assert patched_settings.export == settings.export
    base = settings.synthesis
    merged = merge_project_synthesis_request(
        base,
        SynthesisRequest(
            voice="de_DE-thorsten-medium",
            engine_options={"shared": "invocation", "invocation": True},
            refresh=True,
        ),
    )
    assert merged.language == "de"
    assert merged.engine == "piper"
    assert merged.voice == "de_DE-thorsten-medium"
    assert merged.auto_lexicons is True
    assert merged.allow_experimental is True
    assert merged.offline is True
    assert merged.refresh is True
    assert merged.engine_options == {
        "length_scale": 1.1,
        "shared": "invocation",
        "invocation": True,
    }
    disabled = merge_project_synthesis_request(base, SynthesisRequest(clear_lexicons=True))
    assert disabled.clear_lexicons is True
    assert disabled.auto_lexicons is False


def test_project_settings_fingerprint_is_stable_for_mapping_order(tmp_path) -> None:
    from readio.api.types import ProjectSettings, ProjectSynthesisSettings
    from readio.project_settings import project_settings_fingerprint

    root = tmp_path / "book.readio"
    left = ProjectSettings(
        synthesis=ProjectSynthesisSettings(engine_options={"first": 1, "second": {"x": 2}})
    )
    right = ProjectSettings(
        synthesis=ProjectSynthesisSettings(engine_options={"second": {"x": 2}, "first": 1})
    )

    assert project_settings_fingerprint(left, root) == project_settings_fingerprint(right, root)
