from __future__ import annotations

from pathlib import Path

import pytest
from audiobook_support import make_epub

from readio.api import (
    AUDIOBOOK_EXPORT_FORMAT,
    SUPPORTED_AUDIO_FORMATS,
    SUPPORTED_AUDIOBOOK_EXPORT_FORMATS,
    SUPPORTED_AUDIOBOOK_FORMATS,
    AudiobookExportOptions,
    ExecutionError,
    OutputError,
    Readio,
)
from readio.api.types import ProjectSettings
from readio.audiobook import init_audiobook_project
from readio.stages.audiobook_export import AudiobookExportError


def _project(tmp_path: Path):
    source = tmp_path / "book.epub"
    make_epub(source)
    return init_audiobook_project(source, tmp_path / "book.readio")


def test_audiobook_export_api_translates_result_for_public_consumers(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    app_events = []
    call_events = []
    app = Readio(on_event=app_events.append)
    captured = {}

    def fake_export(internal, **kwargs):
        assert internal.root == project.root
        captured.update(kwargs)
        return {
            "export_id": "sha256:export",
            "path": project.root / "output" / "book.m4b",
            "format": "m4b",
            "output_sha256": "sha256:output",
            "chapter_count": 7,
        }

    monkeypatch.setattr(
        "readio.api.audiobooks.audiobook_export_internal.export_audiobook_project",
        fake_export,
    )
    result = app.audiobooks.export(
        project.root,
        AudiobookExportOptions(
            output=Path("output/custom.m4b"),
            title="Custom title",
            author="Custom author",
            cover=Path("cover.jpg"),
            bitrate="96k",
            force=True,
        ),
        on_event=call_events.append,
    )

    assert AUDIOBOOK_EXPORT_FORMAT == "m4b"
    assert SUPPORTED_AUDIOBOOK_EXPORT_FORMATS == ("m4b",)
    assert SUPPORTED_AUDIOBOOK_FORMATS == ("m4b",)
    assert "m4b" not in SUPPORTED_AUDIO_FORMATS
    assert result.project.root == project.root
    assert result.output_path == project.root / "output" / "book.m4b"
    assert result.format == "m4b"
    assert result.chapter_count == 7
    assert result.to_dict()["format"] == "m4b"
    assert captured == {
        "output": Path("output/custom.m4b"),
        "title": "Custom title",
        "author": "Custom author",
        "cover": Path("cover.jpg"),
        "bitrate": "96k",
        "force": True,
    }
    expected_events = [
        "operation.started",
        "stage.started",
        "stage.completed",
        "operation.completed",
    ]
    assert [event.kind for event in app_events] == expected_events
    assert [event.kind for event in call_events] == expected_events
    assert app_events[2].details["format"] == "m4b"


def test_audiobook_export_api_preserves_stable_error_codes_and_details(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    app = Readio()

    def output_exists(*args, **kwargs):
        raise AudiobookExportError(
            "output is not owned by Readio",
            code="audiobook.export.output_exists",
        )

    monkeypatch.setattr(
        "readio.api.audiobooks.audiobook_export_internal.export_audiobook_project",
        output_exists,
    )
    with pytest.raises(OutputError) as error:
        app.audiobooks.export(project.root)
    assert error.value.code == "audiobook.export.output_exists"

    def encode_failed(*args, **kwargs):
        raise AudiobookExportError(
            "FFmpeg failed",
            code="audiobook.export.encode_failed",
            details={"stderr_tail": "invalid mux"},
        )

    monkeypatch.setattr(
        "readio.api.audiobooks.audiobook_export_internal.export_audiobook_project",
        encode_failed,
    )
    with pytest.raises(ExecutionError) as error:
        app.audiobooks.export(project.root)
    assert error.value.code == "audiobook.export.encode_failed"
    assert error.value.details["stderr_tail"] == "invalid mux"


def test_audiobook_export_uses_saved_defaults_and_transient_overrides(
    tmp_path: Path, monkeypatch
) -> None:
    project = _project(tmp_path)
    app = Readio()
    output = project.root / "exports" / "saved.m4b"
    settings = ProjectSettings(
        audiobook_export=AudiobookExportOptions(
            output=output,
            title="Saved title",
            author="Saved author",
            cover=project.root / "saved-cover.jpg",
            bitrate="128k",
        )
    )
    app.projects.configure(project.root, settings)
    captured = []

    def fake_export(internal, **kwargs):
        captured.append(kwargs)
        return {
            "export_id": "sha256:export",
            "path": kwargs["output"],
            "format": "m4b",
            "output_sha256": "sha256:output",
            "chapter_count": 7,
        }

    monkeypatch.setattr(
        "readio.api.audiobooks.audiobook_export_internal.export_audiobook_project",
        fake_export,
    )
    app.audiobooks.export(project.root)
    app.audiobooks.export(
        project.root,
        AudiobookExportOptions(title="Run title", cover=Path("run-cover.jpg"), force=True),
    )

    assert captured[0] == {
        "output": output,
        "title": "Saved title",
        "author": "Saved author",
        "cover": settings.audiobook_export.cover,
        "bitrate": "128k",
        "force": False,
    }
    assert captured[1] == {
        "output": output,
        "title": "Run title",
        "author": "Saved author",
        "cover": Path("run-cover.jpg"),
        "bitrate": "128k",
        "force": True,
    }
    assert app.projects.settings(project.root) == settings


def test_audiobook_build_runs_project_stages_before_export(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path)
    app = Readio()
    output = project.root / "exports" / "built.m4b"
    settings = ProjectSettings(
        audiobook_export=AudiobookExportOptions(
            output=output, title="Build title", author="Build author", bitrate="96k"
        )
    )
    app.projects.configure(project.root, settings)
    calls = []
    for stage in ("plan", "synthesize", "compose"):
        monkeypatch.setattr(
            app.projects,
            stage,
            lambda target, _stage=stage, **kwargs: calls.append(_stage),
        )
    captured = {}

    def fake_export(internal, **kwargs):
        captured.update(kwargs)
        return {
            "export_id": "sha256:export",
            "path": kwargs["output"],
            "format": "m4b",
            "output_sha256": "sha256:output",
            "chapter_count": 7,
        }

    monkeypatch.setattr(
        "readio.api.audiobooks.audiobook_export_internal.export_audiobook_project",
        fake_export,
    )
    result = app.audiobooks.build(project.root)

    assert calls == ["plan", "synthesize", "compose"]
    assert result.output_path == output
    assert captured["output"] == output
    assert captured["title"] == "Build title"
    assert captured["author"] == "Build author"
    assert captured["bitrate"] == "96k"


def test_audiobook_project_creation_can_persist_settings(tmp_path: Path) -> None:
    source = tmp_path / "configured.epub"
    make_epub(source)
    app = Readio()
    settings = ProjectSettings(
        audiobook_export=AudiobookExportOptions(
            output=tmp_path / "configured.m4b",
            title="Configured title",
        )
    )

    created = app.audiobooks.create_project_result(
        source, output=tmp_path / "configured.readio", settings=settings
    )

    assert app.projects.settings(created.project) == settings
    assert app.audiobooks.describe_project(created.project).chapters == created.chapters
