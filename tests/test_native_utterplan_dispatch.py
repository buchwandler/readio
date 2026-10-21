from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest
import utterplan

from readio.api.types import PlanRequest, RenderPlanRequest
from readio.errors import ReadioStageError
from readio.pipeline import plan, render_plan
from readio.workspace import WorkspaceRef


def _workspace(tmp_path: Path, kind: Literal["document", "book"]) -> WorkspaceRef:
    return WorkspaceRef(root=tmp_path / "story.ssmd-bundle", kind=kind)


@pytest.mark.parametrize("kind", ["document", "book"])
def test_compile_forwards_bundle_to_public_api_for_both_kinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: Literal["document", "book"]
) -> None:
    workspace = _workspace(tmp_path, kind)
    planner_config = object()
    start_source = SimpleNamespace(phase="bundle.start")
    progress_source = SimpleNamespace(completed=2, total=4, message="compiled")
    captured: dict[str, Any] = {}
    events: list[object] = []
    result = object()

    def compile_bundle(path: Path, **kwargs: Any) -> object:
        captured.update(path=path, **kwargs)
        kwargs["on_start"](start_source)
        kwargs["on_progress"](progress_source)
        return result

    monkeypatch.setattr(utterplan, "compile_bundle", compile_bundle)
    actual = plan(
        workspace,
        PlanRequest(
            chapters="5-17",
            language="en-US",
            planner_config=planner_config,
            diagnostics="all",
            force=True,
        ),
        on_start=events.append,
        on_progress=events.append,
    )

    assert actual is result
    assert captured["path"] == workspace.root
    assert captured["chapters"] == "5-17"
    assert captured["language"] == "en-US"
    assert captured["planner_config"] is planner_config
    assert captured["diagnostics"] == "all"
    assert captured["force"] is True
    assert events[0].stage == events[1].stage == "compile"
    assert events[0].source is start_source
    assert events[1].current == 2
    assert events[1].total == 4
    assert events[1].message == "compiled"


def test_render_plan_forwards_bundle_options_and_exact_projection_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, "document")
    profile = tmp_path / "narration.renderprofile.toml"
    result = SimpleNamespace(variant="narration--exact-projection")
    start_source = object()
    progress_source = SimpleNamespace(completed=3, total=8)
    captured: dict[str, Any] = {}
    events: list[object] = []

    def render_bundle(path: Path, **kwargs: Any) -> object:
        captured.update(path=path, **kwargs)
        kwargs["on_start"](start_source)
        kwargs["on_progress"](progress_source)
        return result

    monkeypatch.setattr(utterplan, "render_bundle", render_bundle)
    projected = render_plan(
        workspace,
        RenderPlanRequest(
            profile=profile,
            variant="narration",
            chapters="2-4",
            utterplan_root=tmp_path / "utterplan-override",
            output_root=tmp_path / "projection-override",
            force=True,
        ),
        on_start=events.append,
        on_progress=events.append,
    )

    assert projected.native is result
    assert projected.variant == result.variant
    assert captured["path"] == workspace.root
    assert captured["profile"] == profile
    assert captured["variant"] == "narration"
    assert captured["chapters"] == "2-4"
    assert captured["utterplan_dir"] == tmp_path / "utterplan-override"
    assert captured["output_root"] == tmp_path / "projection-override"
    assert captured["force"] is True
    assert events[0].stage == events[1].stage == "render_plan"
    assert events[0].source is start_source
    assert events[1].current == 3
    assert events[1].total == 8


def test_stage_error_keeps_code_cause_and_bundle_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, "book")
    failure = ValueError("invalid bundle")
    failure.code = "INVALID_BUNDLE"  # type: ignore[attr-defined]
    monkeypatch.setattr(
        utterplan, "compile_bundle", lambda *_args, **_kwargs: (_ for _ in ()).throw(failure)
    )

    with pytest.raises(ReadioStageError) as caught:
        plan(workspace, PlanRequest())

    assert caught.value.code == "INVALID_BUNDLE"
    assert caught.value.workspace == workspace.root
    assert caught.value.__cause__ is failure
