from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import utterplan

from readio.api.types import PlanRequest, RenderPlanRequest
from readio.errors import ReadioStageError
from readio.pipeline import plan, render_plan
from readio.workspace import WorkspaceRef


def _workspace(tmp_path: Path, kind: str) -> WorkspaceRef:
    return WorkspaceRef(
        root=tmp_path / ("story.ssmdoc" if kind == "document" else "book.ssmdbook"), kind=kind
    )  # type: ignore[arg-type]


def test_document_compile_uses_public_service_and_normalizes_callbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, "document")
    planner_config = object()
    start_source = SimpleNamespace(phase="document.start")
    progress_source = SimpleNamespace(completed=2, total=4, message="compiled")
    captured: dict[str, Any] = {}
    normalized: list[object] = []
    result = object()

    def compile_ssmdoc(path: Path, **kwargs: Any) -> object:
        captured.update(path=path, **kwargs)
        kwargs["on_start"](start_source)
        kwargs["on_progress"](progress_source)
        return result

    monkeypatch.setattr(utterplan, "compile_ssmdoc", compile_ssmdoc)

    actual = plan(
        workspace,
        PlanRequest(language="en-US", planner_config=planner_config, diagnostics="all", force=True),
        on_start=normalized.append,
        on_progress=normalized.append,
    )

    assert actual is result
    assert captured == {
        "path": workspace.root,
        "language": "en-US",
        "planner_config": planner_config,
        "diagnostics": "all",
        "force": True,
        "on_start": captured["on_start"],
        "on_progress": captured["on_progress"],
    }
    start_event, progress_event = normalized
    assert start_event.stage == "compile"
    assert start_event.source is start_source
    assert progress_event.current == 2
    assert progress_event.total == 4
    assert progress_event.message == "compiled"


def test_book_compile_forwards_selector_and_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, "book")
    progress_source = SimpleNamespace(current=1, total=3, kind="chapter.started")
    captured: dict[str, Any] = {}
    events: list[object] = []
    result = object()

    def compile_book(path: Path, **kwargs: Any) -> object:
        captured.update(path=path, **kwargs)
        kwargs["on_progress"](progress_source)
        return result

    monkeypatch.setattr(utterplan, "compile_book", compile_book)

    actual = plan(
        workspace,
        PlanRequest(chapters="5-17", force=True),
        on_progress=events.append,
    )

    assert actual is result
    assert captured["path"] == workspace.root
    assert captured["chapters"] == "5-17"
    assert captured["force"] is True
    assert events[0].stage == "compile"
    assert events[0].current == 1
    assert events[0].total == 3
    assert events[0].message == "chapter.started"


def test_document_rejects_book_chapter_selectors_before_package_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        utterplan,
        "compile_ssmdoc",
        lambda *_args, **_kwargs: pytest.fail("document compile should not run"),
    )

    with pytest.raises(ValueError, match=r"only valid for \.ssmdbook"):
        plan(_workspace(tmp_path, "document"), PlanRequest(chapters="2-4"))


def test_render_plan_preserves_exact_public_projection_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, "document")
    profile = tmp_path / "narration.renderprofile.toml"
    result = SimpleNamespace(variant="narration--abc123")
    captured: dict[str, Any] = {}
    start_source = object()
    events: list[object] = []

    def render_ssmdoc(path: Path, **kwargs: Any) -> object:
        captured.update(path=path, **kwargs)
        kwargs["on_start"](start_source)
        return result

    monkeypatch.setattr(utterplan, "render_ssmdoc", render_ssmdoc)

    projected = render_plan(
        workspace,
        RenderPlanRequest(
            profile=profile,
            variant="narration",
            utterplan_root=tmp_path / "utterplan-override",
            output_root=tmp_path / "projection-override",
            force=True,
        ),
        on_start=events.append,
    )

    assert projected.native is result
    assert projected.variant == "narration--abc123"
    assert captured["path"] == workspace.root
    assert captured["profile"] == profile
    assert captured["variant"] == "narration"
    assert captured["utterplan_dir"] == tmp_path / "utterplan-override"
    assert captured["output_root"] == tmp_path / "projection-override"
    assert captured["force"] is True
    assert events[0].stage == "render_plan"
    assert events[0].source is start_source


def test_book_render_plan_forwards_chapters_and_projection_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, "book")
    result = SimpleNamespace(variant="narration--book-hash")
    progress_source = SimpleNamespace(completed=3, total=8)
    captured: dict[str, Any] = {}
    events: list[object] = []

    def render_book(path: Path, **kwargs: Any) -> object:
        captured.update(path=path, **kwargs)
        kwargs["on_progress"](progress_source)
        return result

    monkeypatch.setattr(utterplan, "render_book", render_book)

    projected = render_plan(
        workspace,
        RenderPlanRequest(profile=tmp_path / "profile", chapters="3-6"),
        on_progress=events.append,
    )

    assert projected.variant == result.variant
    assert captured["path"] == workspace.root
    assert captured["chapters"] == "3-6"
    assert events[0].stage == "render_plan"
    assert events[0].current == 3
    assert events[0].total == 8


def test_dependency_error_keeps_code_cause_and_workspace_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, "document")
    failure = ValueError("invalid SSMD")
    failure.code = "INVALID_SOURCE"  # type: ignore[attr-defined]
    monkeypatch.setattr(
        utterplan, "compile_ssmdoc", lambda *_args, **_kwargs: (_ for _ in ()).throw(failure)
    )

    with pytest.raises(ReadioStageError) as caught:
        plan(workspace, PlanRequest())

    assert caught.value.code == "INVALID_SOURCE"
    assert caught.value.workspace == workspace.root
    assert caught.value.__cause__ is failure
