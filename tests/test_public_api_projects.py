from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from audiobook_support import make_epub
from project_support import Adapter

from readio.api import (
    AudiobookChapter,
    AudiobookInspection,
    AudiobookProjectResult,
    CompositionOptions,
    ExportOptions,
    PreviewRequest,
    ProjectBuildRequest,
    ProjectBuildResult,
    ProjectConflictError,
    ProjectNotFoundError,
    ProjectPlanResult,
    ProjectRef,
    ProjectStatus,
    ProjectSynthesisResult,
    Readio,
)
from readio.config import ReaderSettings, ReadioConfig
from readio.engines.registry import _registry
from readio.plan import SynthesisRequest


def test_project_lifecycle_planning_and_typed_status(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("A project API plan.", encoding="utf-8")
    root = tmp_path / "source.readio"
    app = Readio(ReadioConfig())

    project = app.projects.create(source, output=root)

    assert isinstance(project, ProjectRef)
    assert app.projects.find(root) == project
    assert app.projects.open(root) == project
    with patch("pykokoro.KokoroPipeline", side_effect=AssertionError("TTS loaded")):
        result = app.projects.plan(project)

    assert isinstance(result, ProjectPlanResult)
    assert result.scopes
    assert json.loads(json.dumps(result.to_dict()))["project"]["project_id"] == project.project_id
    status = app.projects.status(project)
    assert isinstance(status, ProjectStatus)
    assert status.stage("plan").state == "current"
    assert json.loads(json.dumps(status.to_dict()))["stages"]


def test_project_build_returns_typed_incremental_operations(tmp_path: Path, monkeypatch) -> None:
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    app = Readio(ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice")))
    source = tmp_path / "book.txt"
    source.write_text("Alpha.\n\nBeta.", encoding="utf-8")
    project = app.projects.create(source, output=tmp_path / "book.readio")
    request = ProjectBuildRequest(
        synthesis=SynthesisRequest(engine="fake", voice="fake-voice"),
        export=ExportOptions(format="wav"),
    )

    first = app.projects.build(project, request)
    second = app.projects.build(project, request)

    assert isinstance(first, ProjectBuildResult)
    assert first.output_path is not None and first.output_path.is_file()
    assert [operation.action for operation in second.operations] == [
        "skipped",
        "skipped",
        "skipped",
        "skipped",
    ]
    assert adapter.open_calls == 1
    assert json.loads(json.dumps(first.to_dict()))["operations"]

    composition_only = app.projects.build(
        project,
        ProjectBuildRequest(
            synthesis=request.synthesis,
            composition=CompositionOptions(target_lufs=-18),
            export=request.export,
        ),
    )
    assert composition_only.operations[1].action == "skipped"
    assert composition_only.operations[2].action == "rebuilt"
    assert adapter.open_calls == 1


def test_project_synthesis_target_stops_before_composition(tmp_path: Path, monkeypatch) -> None:
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    app = Readio(ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice")))
    source = tmp_path / "book.txt"
    source.write_text("Only synthesize.", encoding="utf-8")
    project = app.projects.create(source, output=tmp_path / "book.readio")

    result = app.projects.build(
        project,
        ProjectBuildRequest(
            target="synthesis",
            synthesis=SynthesisRequest(engine="fake", voice="fake-voice"),
        ),
    )

    assert [operation.stage for operation in result.operations] == ["plan", "synthesis"]
    assert not any(operation.stage == "composition" for operation in result.operations)
    direct = app.projects.synthesize(
        project,
        SynthesisRequest(engine="fake", voice="fake-voice"),
    )
    assert isinstance(direct, ProjectSynthesisResult)


def test_audiobook_inspection_and_project_creation_are_typed(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    make_epub(source)
    app = Readio(ReadioConfig())

    inspection = app.audiobooks.inspect(source)
    project = app.audiobooks.create_project(
        source,
        chapters="1-2",
        output=tmp_path / "book.readio",
    )

    assert isinstance(inspection, AudiobookInspection)
    assert all(isinstance(chapter, AudiobookChapter) for chapter in inspection.chapters)
    assert len(inspection.chapters) == 7
    assert json.loads(json.dumps(inspection.to_dict()))["metadata"]["title"] == "The Example"
    assert project.kind == "audiobook"
    assert app.projects.open(project.root) == project
    assert app.projects.status(project).stage("document").state == "current"
    creation = app.audiobooks.create_project_result(
        source, chapters="2-3", output=tmp_path / "selected.readio"
    )
    assert isinstance(creation, AudiobookProjectResult)
    assert creation.selected_chapters == 2
    assert [chapter.number for chapter in creation.chapters] == [2, 3]


def test_project_errors_are_translated_to_public_types(tmp_path: Path) -> None:
    app = Readio(ReadioConfig())
    with pytest.raises(ProjectNotFoundError) as missing:
        app.projects.open(tmp_path / "missing.readio")
    assert missing.value.code == "project.not_found"

    source = tmp_path / "locked.txt"
    source.write_text("Locked project.", encoding="utf-8")
    project = app.projects.create(source, output=tmp_path / "locked.readio")
    (project.root / ".lock").write_text("other-process plan", encoding="utf-8")
    with pytest.raises(ProjectConflictError) as locked:
        app.projects.plan(project)
    assert locked.value.code == "project.locked"


def test_preview_is_typed_and_does_not_activate_synthesis(tmp_path: Path, monkeypatch) -> None:
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    app = Readio(ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice")))
    source = tmp_path / "preview.txt"
    source.write_text("A preview paragraph.", encoding="utf-8")
    project = app.projects.create(source, output=tmp_path / "preview.readio")
    app.projects.plan(project)
    profile_path = project.root / "synthesis" / "profile.json"
    before = profile_path.read_bytes() if profile_path.exists() else None

    result = app.projects.preview(
        project,
        PreviewRequest(
            selection="first:1",
            synthesis=SynthesisRequest(engine="fake", voice="fake-voice"),
        ),
    )

    assert result.activated is False
    assert result.frames > 0
    assert json.loads(json.dumps(result.to_dict()))["items"] >= 0
    after = profile_path.read_bytes() if profile_path.exists() else None
    assert after == before
