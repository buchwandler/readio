from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from readio.api.types import (
    AudiobookExportOptions,
    CompositionOptions,
    ExportOptions,
    ProjectSettings,
    ProjectSynthesisSettings,
)
from readio.project import init_project
from readio.project_model import (
    DocumentIndex,
    DocumentScope,
    PlanIndex,
    PlanScope,
    ProjectFormatError,
    ProjectManifest,
)
from readio.project_settings import (
    project_settings_from_manifest,
    project_settings_to_dict,
    with_project_settings,
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
    value = {"format": "readio.plan-index", "schema_version": 1, "scopes": [scope.to_dict()]}
    assert PlanIndex.from_dict(value).to_dict() == value
    configured = PlanIndex(
        scopes=(scope,),
        project_planning_settings_sha256=f"sha256:{'a' * 64}",
    )
    assert PlanIndex.from_dict(configured.to_dict()) == configured
    with pytest.raises(ProjectFormatError, match="project_planning_settings_sha256"):
        PlanIndex.from_dict({**value, "project_planning_settings_sha256": 42})


def test_document_index_rejects_duplicate_scope_ids() -> None:
    scope = DocumentScope(
        id="chapter-0001",
        kind="chapter",
        path="document/chapters/chapter-0001.md",
        input_format="markdown",
    )
    with pytest.raises(ProjectFormatError, match="duplicate scope IDs"):
        DocumentIndex.from_dict(DocumentIndex(scopes=(scope, scope)).to_dict())


def test_new_projects_serialize_only_schema_three(tmp_path: Path) -> None:
    source = tmp_path / "book.md"
    source.write_text("# Heading\n\nHello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    payload = json.loads((project.root / "project.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 3
    assert payload["kind"] == "document"
    assert payload["document"] == {"index_path": "document/index.json"}
    assert ProjectManifest.from_dict(payload).to_dict() == payload
    assert project.document_scopes()[0].id == "document"


@pytest.mark.parametrize("schema", [1, 2])
def test_old_project_schemas_require_explicit_migration(tmp_path: Path, schema: int) -> None:
    source = tmp_path / "book.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    payload = project.manifest.to_dict()
    payload["schema_version"] = schema
    with pytest.raises(ProjectFormatError, match="readio project migrate"):
        ProjectManifest.from_dict(payload)


def test_schema_three_rejects_provider_bindings_but_accepts_structured_roles(
    tmp_path: Path,
) -> None:
    source = tmp_path / "book.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    payload = project.manifest.to_dict()

    payload["settings"] = {"ssmd": {"voice_provider": "kokoro"}}
    with pytest.raises(ProjectFormatError, match="readio project migrate"):
        ProjectManifest.from_dict(payload)

    payload["settings"] = {
        "ssmd": {"role_bindings": {"narrator": {"engine": "kokoro", "voice": "af_heart"}}}
    }
    manifest = ProjectManifest.from_dict(payload)
    assert manifest.settings["ssmd"]["role_bindings"]["narrator"]["engine"] == "kokoro"


def test_project_pipeline_settings_round_trip_and_preserve_structured_bindings(
    tmp_path: Path,
) -> None:
    source = tmp_path / "book.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    settings = ProjectSettings(
        synthesis=ProjectSynthesisSettings(
            language="en-us",
            engine="kokoro",
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
    original_settings = {
        "custom": {"keep": True},
        "ssmd": {"role_bindings": {"host": {"engine": "kokoro", "voice": "af_sarah"}}},
    }
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
    assert changed.settings["ssmd"] == original_settings["ssmd"]
    assert project_settings_to_dict(restored, project.root) == {
        key: value
        for key, value in persisted.items()
        if key in {"synthesis", "composition", "export", "audiobook_export"}
    }


@pytest.mark.parametrize(
    "selectors",
    [
        {"voice": "named", "voice_file": Path("voice.wav")},
        {"voice": "named", "voice_prompt": "kyutai-tts-voices:alba/casual"},
        {"voice_file": Path("voice.wav"), "voice_prompt": "kyutai-tts-voices:alba/casual"},
        {
            "voice": "named",
            "voice_file": Path("voice.wav"),
            "voice_prompt": "kyutai-tts-voices:alba/casual",
        },
    ],
)
def test_project_synthesis_settings_reject_multiple_voice_sources(selectors) -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        ProjectSynthesisSettings(**selectors)


def test_managed_voice_prompt_project_settings_round_trip_and_override(tmp_path: Path) -> None:
    from readio.plan import SynthesisRequest
    from readio.project_settings import merge_project_synthesis_request

    source = tmp_path / "book.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    prompt = "kyutai-tts-voices:alba-mackenna/casual"
    settings = ProjectSettings(
        synthesis=ProjectSynthesisSettings(engine="pocket", voice_prompt=prompt)
    )

    changed = with_project_settings(project.manifest, settings, project.root)
    restored = project_settings_from_manifest(changed, project.root)
    assert changed.settings["synthesis"]["voice_prompt"] == prompt
    assert restored.synthesis == settings.synthesis

    merged = merge_project_synthesis_request(
        ProjectSynthesisSettings(engine="pocket", voice="named-voice"),
        SynthesisRequest(engine="pocket", voice_prompt=prompt),
    )
    assert merged.voice is None
    assert merged.voice_file is None
    assert merged.voice_prompt == prompt


def test_project_settings_normalize_input_engine_alias_before_persistence(tmp_path: Path) -> None:
    source = tmp_path / "book.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    settings = ProjectSettings(synthesis=ProjectSynthesisSettings(engine="pykokoro"))

    changed = with_project_settings(project.manifest, settings, project.root)

    assert changed.settings["synthesis"]["engine"] == "kokoro"


@pytest.mark.parametrize(
    "settings",
    [
        {"synthesis": {"speed": float("nan")}},
        {"synthesis": {"spacy": "sometimes"}},
        {"synthesis": {"engine": "pykokoro"}},
        {"synthesis": {"lexicons": "crane"}},
        {"synthesis": {"clear_lexicons": True, "auto_lexicons": True}},
        {"synthesis": {"refresh": False}},
        {"synthesis": {"voice": "named", "voice_prompt": "kyutai-tts-voices:alba/casual"}},
        {"synthesis": {"voice_file": "voice.wav", "voice_prompt": "kyutai-tts-voices:alba/casual"}},
        {
            "synthesis": {
                "voice": "named",
                "voice_file": "voice.wav",
                "voice_prompt": "kyutai-tts-voices:alba/casual",
            }
        },
        {"synthesis": {"engine_options": {"temperature": float("inf")}}},
        {"composition": {"sample_rate": True}},
        {"composition": {"peak_policy": []}},
        {"export": {"format": "m4b"}},
        {"export": {"force": False}},
        {"audiobook_export": {"format": "wav"}},
        {"audiobook_export": {"force": False}},
    ],
)
def test_project_manifest_rejects_malformed_pipeline_settings(tmp_path: Path, settings) -> None:
    source = tmp_path / "book.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    payload = project.manifest.to_dict()
    payload["settings"] = settings
    with pytest.raises(ProjectFormatError):
        ProjectManifest.from_dict(payload)
