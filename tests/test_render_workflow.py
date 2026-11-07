from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from readio import render_workflow as workflow
from readio.api.service import Readio
from readio.api.types import RenderRequest, WorkflowPlan, WorkflowStep
from readio.errors import OutputCollisionError, WorkflowStageError
from readio.workspace import WorkspacePolicy, WorkspaceRef

_FORMATS = {
    "mp3": SimpleNamespace(extension=".mp3"),
    "m4b": SimpleNamespace(extension=".m4b"),
}


def _request(
    tmp_path: Path,
    *,
    suffix: str = ".md",
    bundle: Path | None = None,
    output: Path | None = None,
    chapters: str = "all",
    rebuild_from: str | None = None,
    replace_output: bool = False,
) -> RenderRequest:
    source = tmp_path / f"source{suffix}"
    source.write_text("source", encoding="utf-8")
    profile = tmp_path / "voice.renderprofile.toml"
    profile.write_text("profile", encoding="utf-8")
    return RenderRequest(
        source=source,
        profile=profile,
        chapters=chapters,
        bundle=bundle,
        output=output,
        rebuild_from=rebuild_from,  # type: ignore[arg-type]
        replace_output=replace_output,
    )


def _mock_source_inputs(monkeypatch: pytest.MonkeyPatch, kind: str = "document") -> None:
    monkeypatch.setattr(
        workflow,
        "inspect_source",
        lambda _source: SimpleNamespace(kind=kind),
    )
    monkeypatch.setattr(workflow, "load_render_profile", lambda _profile: object())
    monkeypatch.setattr(workflow, "supported_formats", lambda: _FORMATS)


def test_inspect_and_render_dry_run_use_temporary_policy_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_source_inputs(monkeypatch)
    request = _request(tmp_path, output=tmp_path / "result.mp3")

    api = Readio()
    inspected = api.inspect(request)
    dry_run = api.render(request, dry_run=True)

    assert isinstance(inspected, WorkflowPlan)
    assert isinstance(dry_run, WorkflowPlan)
    assert inspected.workspace_mode == "temporary"
    assert inspected.workspace is not None and not inspected.workspace.exists()
    assert [step.action for step in inspected.steps] == ["execute"] * 6
    assert not (tmp_path / "result.mp3").exists()
    assert not list(tmp_path.glob("*.ssmd-bundle"))


def test_persistent_book_plan_materializes_all_chapters_before_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_source_inputs(monkeypatch, kind="book")
    bundle = tmp_path / "novel.ssmd-bundle"
    request = _request(
        tmp_path,
        suffix=".epub",
        bundle=bundle,
        output=tmp_path / "novel.m4b",
        chapters="2",
    )

    plan = Readio().inspect(request)

    assert plan.workspace_mode == "persistent"
    assert plan.steps[0].stage == "convert"
    assert plan.steps[0].selector == "all"
    assert plan.steps[0].action == "execute"
    assert plan.steps[1].selector == "2"
    assert not bundle.exists()


def test_existing_final_output_requires_explicit_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_source_inputs(monkeypatch)
    output = tmp_path / "result.mp3"
    output.write_bytes(b"user media")
    request = _request(tmp_path, output=output)

    blocked = Readio().inspect(request)
    allowed = Readio().inspect(replace(request, replace_output=True))

    assert blocked.steps[-1].action == "blocked"
    assert allowed.steps[-1].action == "replace"
    with pytest.raises(OutputCollisionError):
        workflow.render_workflow(object(), request)
    assert output.read_bytes() == b"user media"


def test_source_provenance_mismatch_requires_explicit_safe_convert_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_source_inputs(monkeypatch)
    source = tmp_path / "source.md"
    source.write_text("source", encoding="utf-8")
    profile = tmp_path / "voice.renderprofile.toml"
    profile.write_text("profile", encoding="utf-8")
    bundle = tmp_path / "existing.ssmd-bundle"
    bundle.mkdir()
    policy = WorkspacePolicy(
        mode="persistent",
        root=bundle,
        source_is_bundle=False,
        newly_created=False,
        cleanup=False,
    )
    monkeypatch.setattr(workflow, "resolve_workspace_policy", lambda *_args, **_kwargs: policy)
    monkeypatch.setattr(
        workflow,
        "resolve_workspace",
        lambda root: WorkspaceRef(root=root, kind="document"),
    )
    monkeypatch.setattr(
        workflow,
        "inspect_source_provenance",
        lambda *_args: SimpleNamespace(
            state="stale",
            reason="source digest changed",
            force_required=True,
            replacement_safe=True,
        ),
    )
    monkeypatch.setattr(
        workflow,
        "inspect_bundle_compile",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="complete", replacement_safe=True, force_required=False
        ),
    )
    monkeypatch.setattr(
        workflow,
        "inspect_bundle_projection",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="complete", variant="voice--profile", replacement_safe=True
        ),
    )
    monkeypatch.setattr(
        workflow,
        "inspect_bundle_render",
        lambda *_args, **_kwargs: SimpleNamespace(state="complete", replacement_safe=True),
    )
    monkeypatch.setattr(
        workflow,
        "inspect_bundle_composition",
        lambda *_args, **_kwargs: SimpleNamespace(state="complete", replacement_safe=True),
    )
    monkeypatch.setattr(
        workflow,
        "inspect_bundle_export",
        lambda *_args, **_kwargs: SimpleNamespace(state="not_started", replacement_safe=True),
    )
    request = RenderRequest(
        source=source,
        profile=profile,
        bundle=bundle,
        output=tmp_path / "result.mp3",
    )

    blocked = Readio().inspect(request)
    rebuilt = Readio().inspect(replace(request, rebuild_from="convert"))

    assert blocked.steps[0].action == "blocked"
    assert rebuilt.steps[0].action == "replace"
    assert all(step.action != "blocked" for step in rebuilt.steps)


def test_temporary_workspace_is_removed_when_conversion_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_source_inputs(monkeypatch)
    request = _request(tmp_path, output=tmp_path / "result.mp3")
    workspace_paths: list[Path] = []

    class FailingService:
        def create_workspace(self, _source: Path, create_request: object) -> None:
            workspace_paths.append(create_request.output)  # type: ignore[attr-defined,arg-type]
            raise RuntimeError("conversion failed")

    with pytest.raises(WorkflowStageError, match="conversion failed"):
        workflow.render_workflow(FailingService(), request)

    assert len(workspace_paths) == 1
    assert not workspace_paths[0].parent.exists()
    assert not (tmp_path / "result.mp3").exists()


def test_temporary_workflow_runs_all_stages_and_keeps_output_after_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_source_inputs(monkeypatch)
    request = _request(tmp_path, output=tmp_path / "result.mp3")
    executed: list[str] = []
    workspace_paths: list[Path] = []

    def current_plan(
        resolved: workflow._ResolvedRender,
        _request: RenderRequest,
        policy: WorkspacePolicy,
    ) -> WorkflowPlan:
        steps = tuple(
            WorkflowStep(
                stage=stage,
                package=workflow._STAGE_PACKAGES[stage],
                action="reuse" if stage in executed else "execute",
                selector="all",
                reason="fake specialist state",
                state="complete" if stage in executed else "not_started",
                details={},
            )
            for stage in workflow._STAGE_ORDER
        )
        return WorkflowPlan(
            source=resolved.source,
            workspace=policy.root,
            workspace_mode=policy.mode,
            variant="voice--profile",
            output=resolved.output,
            steps=steps,
        )

    monkeypatch.setattr(workflow, "_inspect_with_policy", current_plan)
    monkeypatch.setattr(
        workflow,
        "resolve_workspace",
        lambda root: WorkspaceRef(root=root, kind="document"),
    )

    class FakeService:
        def create_workspace(self, _source: Path, create_request: object) -> None:
            workspace_paths.append(create_request.output)  # type: ignore[attr-defined,arg-type]
            create_request.output.mkdir(parents=True)  # type: ignore[attr-defined]
            executed.append("convert")

        def plan(self, *_args: object, **_kwargs: object) -> None:
            executed.append("compile")

        def render_plan(self, *_args: object, **_kwargs: object) -> object:
            executed.append("render_plan")
            return SimpleNamespace(variant="voice--profile")

        def render(self, *_args: object, **_kwargs: object) -> None:
            executed.append("render")

        def compose(self, *_args: object, **_kwargs: object) -> None:
            executed.append("compose")

        def export(self, _workspace: WorkspaceRef, export_request: object) -> None:
            executed.append("export")
            export_request.output.write_bytes(b"audio")  # type: ignore[attr-defined]

    result = workflow.render_workflow(FakeService(), request)

    assert result.workspace is None
    assert result.workspace_mode == "temporary"
    assert result.executed_stages == tuple(workflow._STAGE_ORDER)
    assert executed == list(workflow._STAGE_ORDER)
    assert workspace_paths and not workspace_paths[0].parent.exists()
    assert (tmp_path / "result.mp3").read_bytes() == b"audio"
