from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import audiocompose
import pytest

from readio.api.types import BookExportRequest, ComposeRequest, DocumentExportRequest
from readio.errors import ReadioStageError
from readio.integrations import audioexport as audioexport_integration
from readio.pipeline import compose, export
from readio.workspace import WorkspaceRef


def _workspace(tmp_path: Path, kind: str) -> WorkspaceRef:
    suffix = ".ssmdoc" if kind == "document" else ".ssmdbook"
    return WorkspaceRef(root=tmp_path / f"source{suffix}", kind=kind)  # type: ignore[arg-type]


def test_document_compose_forwards_roots_options_and_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, "document")
    native_result = object()
    progress = SimpleNamespace(completed=1, total=2, phase="compose.document")
    captured: dict[str, Any] = {}
    events: list[object] = []

    def compose_document(path: Path, **kwargs: Any) -> object:
        captured.update(path=path, **kwargs)
        kwargs["on_progress"](progress)
        return native_result

    monkeypatch.setattr(audiocompose, "compose_document", compose_document)

    actual = compose(
        workspace,
        ComposeRequest(
            variant="narration--exact",
            pause_policy=object(),
            output_policy=object(),
            options=object(),
            renderplan_root=tmp_path / "renderplan",
            voicerender_root=tmp_path / "voicerender",
            output_root=tmp_path / "audiocompose",
            media_root=tmp_path / "media",
            save_audiojob=tmp_path / "job.json",
            force=True,
            emphasis_policy="strict",
        ),
        on_progress=events.append,
    )

    assert actual is native_result
    assert captured["path"] == workspace.root
    assert captured["variant"] == "narration--exact"
    assert captured["pause_policy"] is not None
    assert captured["output_policy"] is not None
    assert captured["options"] is not None
    assert captured["renderplan_root"] == tmp_path / "renderplan"
    assert captured["voicerender_root"] == tmp_path / "voicerender"
    assert captured["output_root"] == tmp_path / "audiocompose"
    assert captured["media_root"] == tmp_path / "media"
    assert captured["save_audiojob"] == tmp_path / "job.json"
    assert captured["force"] is True
    assert captured["emphasis_policy"] == "strict"
    assert events[0].stage == "compose"
    assert events[0].current == 1
    assert events[0].total == 2
    assert events[0].source is progress


def test_book_compose_forwards_selector_fail_fast_and_plural_job_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, "book")
    native_result = object()
    captured: dict[str, Any] = {}

    def compose_book(path: Path, **kwargs: Any) -> object:
        captured.update(path=path, **kwargs)
        return native_result

    monkeypatch.setattr(audiocompose, "compose_book", compose_book)

    actual = compose(
        workspace,
        ComposeRequest(
            variant="narration--book-hash",
            chapters="5-17",
            save_audiojob=tmp_path / "jobs",
            force=True,
            fail_fast=True,
        ),
    )

    assert actual is native_result
    assert captured["path"] == workspace.root
    assert captured["variant"] == "narration--book-hash"
    assert captured["chapters"] == "5-17"
    assert captured["save_audiojobs"] == tmp_path / "jobs"
    assert captured["force"] is True
    assert captured["fail_fast"] is True


def test_document_export_forwards_generic_format_and_custom_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, "document")
    native_result = object()
    captured: dict[str, Any] = {}

    def export_document(path: Path, **kwargs: Any) -> object:
        captured.update(path=path, **kwargs)
        return native_result

    monkeypatch.setattr(
        audioexport_integration,
        "import_module",
        lambda _name: SimpleNamespace(export_document=export_document),
    )

    actual = export(
        workspace,
        DocumentExportRequest(
            variant="narration--exact",
            output=tmp_path / "article.flac",
            format="flac",
            bitrate="512k",
            metadata={"title": "Article"},
            cover=tmp_path / "cover.png",
            timeline=tmp_path / "timeline.json",
            audiocompose_root=tmp_path / "custom-compose",
            output_root=tmp_path / "custom-export",
            force=True,
            ffmpeg=tmp_path / "ffmpeg",
            ffprobe=tmp_path / "ffprobe",
        ),
    )

    assert actual is native_result
    assert captured["path"] == workspace.root
    assert captured["variant"] == "narration--exact"
    assert captured["output"] == tmp_path / "article.flac"
    assert captured["format"] == "flac"
    assert captured["bitrate"] == "512k"
    assert captured["metadata"] == {"title": "Article"}
    assert captured["cover"] == tmp_path / "cover.png"
    assert captured["timeline"] == tmp_path / "timeline.json"
    assert captured["audiocompose_root"] == tmp_path / "custom-compose"
    assert captured["output_root"] == tmp_path / "custom-export"
    assert captured["force"] is True
    assert captured["ffmpeg"] == tmp_path / "ffmpeg"
    assert captured["ffprobe"] == tmp_path / "ffprobe"


def test_book_export_returns_native_provenance_and_forwards_custom_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, "book")
    compose_report = object()
    native_result = SimpleNamespace(input_compose_report=compose_report)
    captured: dict[str, Any] = {}

    def export_book(path: Path, **kwargs: Any) -> object:
        captured.update(path=path, **kwargs)
        return native_result

    monkeypatch.setattr(
        audioexport_integration,
        "import_module",
        lambda _name: SimpleNamespace(export_book=export_book),
    )

    actual = export(
        workspace,
        BookExportRequest(
            variant="narration--exact-book",
            chapters="5-17",
            profile=tmp_path / "audiobook-profile.toml",
            metadata={"title": "Book"},
            sample_rate=48000,
            channels=2,
            audiocompose_root=tmp_path / "book-compose",
            output_root=tmp_path / "book-output",
        ),
    )

    assert actual is native_result
    assert actual.input_compose_report is compose_report
    assert captured["path"] == workspace.root
    assert captured["variant"] == "narration--exact-book"
    assert captured["chapters"] == "5-17"
    assert captured["profile"] == tmp_path / "audiobook-profile.toml"
    assert captured["metadata"] == {"title": "Book"}
    assert captured["sample_rate"] == 48000
    assert captured["channels"] == 2
    assert captured["audiocompose_root"] == tmp_path / "book-compose"
    assert captured["output_root"] == tmp_path / "book-output"


def test_export_kind_and_request_type_must_match(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="require DocumentExportRequest"):
        export(_workspace(tmp_path, "document"), BookExportRequest(variant="v"))


def test_missing_optional_audioexport_reports_extra_install_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing_package(name: str) -> Any:
        raise ModuleNotFoundError("No module named 'audioexport'", name=name)

    monkeypatch.setattr(audioexport_integration, "import_module", missing_package)

    with pytest.raises(ReadioStageError, match="install Readio with the 'audioexport' extra"):
        export(_workspace(tmp_path, "document"), DocumentExportRequest(variant="v"))
