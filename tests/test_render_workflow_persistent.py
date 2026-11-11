from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from readio import render_workflow as workflow
from readio.api.service import Readio
from readio.api.types import RenderRequest
from readio.errors import WorkflowStateError
from readio.workspace import WorkspacePolicy, WorkspaceRef

_FORMATS = {"m4b": SimpleNamespace(extension=".m4b")}


def _persistent_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[RenderRequest, Path]:
    source = tmp_path / "novel.epub"
    source.write_bytes(b"source")
    profile = tmp_path / "narrator.renderprofile.toml"
    profile.write_text("profile", encoding="utf-8")
    bundle = tmp_path / "novel.ssmd-bundle"
    bundle.mkdir()
    policy = WorkspacePolicy(
        mode="persistent",
        root=bundle,
        source_is_bundle=False,
        newly_created=False,
        cleanup=False,
    )
    monkeypatch.setattr(workflow, "resolve_workspace_policy", lambda *_a, **_k: policy)
    monkeypatch.setattr(
        workflow,
        "inspect_source",
        lambda _source: SimpleNamespace(kind="book"),
    )
    monkeypatch.setattr(workflow, "load_render_profile", lambda _profile: object())
    monkeypatch.setattr(workflow, "supported_formats", lambda: _FORMATS)
    monkeypatch.setattr(
        workflow,
        "resolve_workspace",
        lambda root: WorkspaceRef(root=root, kind="book"),
    )
    request = RenderRequest(
        source=source,
        profile=profile,
        bundle=bundle,
        chapters="2",
        output=tmp_path / "novel.m4b",
    )
    return request, bundle


def _install_default_inspections(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workflow,
        "inspect_source_provenance",
        lambda *_args: SimpleNamespace(
            state="match", reason=None, replacement_safe=True, force_required=False
        ),
    )
    monkeypatch.setattr(
        workflow,
        "inspect_bundle_compile",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="partial",
            recommended_selector="2",
            replacement_safe=True,
            force_required=False,
        ),
    )
    monkeypatch.setattr(
        workflow,
        "inspect_bundle_projection",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="partial",
            variant="narrator--new-profile",
            recommended_selector="2",
            replacement_safe=True,
            force_required=False,
        ),
    )
    monkeypatch.setattr(
        workflow,
        "inspect_bundle_render",
        lambda *_args, **_kwargs: SimpleNamespace(state="not_started"),
    )
    monkeypatch.setattr(
        workflow,
        "inspect_bundle_composition",
        lambda *_args, **_kwargs: SimpleNamespace(state="not_started"),
    )
    monkeypatch.setattr(
        workflow,
        "inspect_bundle_export",
        lambda *_args, **_kwargs: SimpleNamespace(state="not_started"),
    )


def test_matching_persistent_bundle_reuses_conversion_and_scopes_selected_chapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request, bundle = _persistent_inputs(tmp_path, monkeypatch)
    _install_default_inspections(monkeypatch)

    plan = Readio().inspect(request)

    assert plan.workspace == bundle
    assert plan.workspace_mode == "persistent"
    assert plan.variant == "narrator--new-profile"
    assert plan.steps[0].action == "reuse"
    assert plan.steps[1].selector == "2"
    assert plan.steps[1].action == "execute"
    assert plan.steps[2].selector == "2"
    assert plan.steps[2].action == "execute"


def test_corrupt_render_state_fails_closed_before_any_stage_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request, _bundle = _persistent_inputs(tmp_path, monkeypatch)
    _install_default_inspections(monkeypatch)
    monkeypatch.setattr(
        workflow,
        "inspect_bundle_render",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="corrupt", reason="render manifest is invalid", replacement_safe=False
        ),
    )
    executed: list[str] = []

    class NoRunService:
        def create_workspace(self, *_args: object, **_kwargs: object) -> None:
            executed.append("convert")

        def plan(self, *_args: object, **_kwargs: object) -> None:
            executed.append("compile")

    plan = Readio().inspect(request)
    assert plan.steps[3].action == "blocked"
    with pytest.raises(WorkflowStateError, match="render"):
        workflow.render_workflow(NoRunService(), request)
    assert executed == []


def test_stale_voice_render_output_requires_explicit_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request, _bundle = _persistent_inputs(tmp_path, monkeypatch)
    _install_default_inspections(monkeypatch)
    monkeypatch.setattr(
        workflow,
        "inspect_bundle_render",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="stale",
            reason="VoiceRender artifact policy changed",
            force_required=True,
            replacement_safe=True,
        ),
    )
    calls: list[object] = []

    class RecordingService:
        def render(self, *_args: object, **_kwargs: object) -> None:
            calls.append((_args, _kwargs))

    plan = Readio().inspect(request)
    render_step = next(step for step in plan.steps if step.stage == "render")
    assert render_step.state == "stale"
    assert render_step.action == "blocked"
    assert not render_step.force
    with pytest.raises(WorkflowStateError, match="render"):
        workflow.render_workflow(RecordingService(), request)
    assert calls == []


def test_explicit_render_rebuild_resets_stale_voice_render_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request, _bundle = _persistent_inputs(tmp_path, monkeypatch)
    request = replace(request, rebuild_from="render")
    _install_default_inspections(monkeypatch)
    state = {"render": False, "compose": False, "export": False}
    monkeypatch.setattr(
        workflow,
        "inspect_bundle_compile",
        lambda *_args, **_kwargs: SimpleNamespace(state="complete", replacement_safe=True),
    )
    monkeypatch.setattr(
        workflow,
        "inspect_bundle_projection",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="complete", variant="narrator--new-profile", replacement_safe=True
        ),
    )
    monkeypatch.setattr(
        workflow,
        "inspect_bundle_render",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="complete" if state["render"] else "stale",
            force_required=not state["render"],
            replacement_safe=True,
        ),
    )
    monkeypatch.setattr(
        workflow,
        "inspect_bundle_composition",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="complete" if state["compose"] else "not_started", replacement_safe=True
        ),
    )
    monkeypatch.setattr(
        workflow,
        "inspect_bundle_export",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="complete" if state["export"] else "not_started", replacement_safe=True
        ),
    )

    plan = Readio().inspect(request)
    steps = {step.stage: step for step in plan.steps}
    assert steps["render"].action == "replace"
    assert steps["render"].force
    assert steps["compose"].action == "execute"
    assert steps["export"].action == "execute"

    calls: list[tuple[str, object]] = []

    class RecordingService:
        def render(self, _workspace: object, render_request: object) -> None:
            calls.append(("render", render_request))
            state["render"] = True

        def compose(self, _workspace: object, _compose_request: object) -> None:
            calls.append(("compose", _compose_request))
            state["compose"] = True

        def export(self, _workspace: object, export_request: object) -> None:
            calls.append(("export", export_request))
            state["export"] = True

    result = workflow.render_workflow(RecordingService(), request)

    assert [stage for stage, _request in calls] == ["render", "compose", "export"]
    render_request = calls[0][1]
    assert render_request.force is True
    assert render_request.reset_stale_output is True
    assert result.executed_stages == ("render", "compose", "export")
