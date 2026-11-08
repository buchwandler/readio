from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from readio.api import Readio, create_document_intent
from readio.api import service as service_module
from readio.api.types import (
    ComposeRequest,
    ExportRequest,
    PlanRequest,
    RenderPlanRequest,
    RenderPlanResult,
    RunRequest,
    VoiceRenderRequest,
)
from readio.workspace import WorkspaceRef


@pytest.mark.parametrize("kind", ["document", "book"])
def test_run_propagates_exact_projection_variant_through_one_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["document", "book"],
) -> None:
    workspace = WorkspaceRef(tmp_path / "story.ssmd-bundle", kind)
    source = tmp_path / "input"
    projected_variant = "VoiceRender-defined/variant.with punctuation"
    calls: dict[str, object] = {}

    monkeypatch.setattr(service_module, "_create_workspace", lambda *_args, **_kwargs: workspace)

    def compile_bundle(
        received_workspace: WorkspaceRef, request: PlanRequest, **_kwargs: object
    ) -> str:
        calls["compile"] = (received_workspace, request)
        return "compiled"

    def project_bundle(
        received_workspace: WorkspaceRef,
        request: RenderPlanRequest,
        **_kwargs: object,
    ) -> RenderPlanResult:
        calls["projection"] = (received_workspace, request)
        return RenderPlanResult(native="projected", variant=projected_variant)

    def render_bundle(
        received_workspace: WorkspaceRef,
        request: VoiceRenderRequest,
        **_kwargs: object,
    ) -> str:
        calls["render"] = request.variant
        return "rendered"

    def compose_bundle(
        received_workspace: WorkspaceRef,
        request: ComposeRequest,
        **_kwargs: object,
    ) -> str:
        calls["compose"] = request.variant
        return "composed"

    monkeypatch.setattr(service_module, "plan", compile_bundle)
    monkeypatch.setattr(service_module, "render_plan", project_bundle)
    monkeypatch.setattr(service_module, "render", render_bundle)
    monkeypatch.setattr(service_module, "compose", compose_bundle)

    def export_bundle(received_workspace: WorkspaceRef, request: ExportRequest) -> str:
        calls["export"] = request.variant
        return "exported"

    monkeypatch.setattr(service_module, "export", export_bundle)
    request = RunRequest(
        render_plan=RenderPlanRequest(profile=tmp_path / "profile.toml", variant="display-label"),
        render=VoiceRenderRequest(variant="ignored"),
        compose=ComposeRequest(variant="ignored"),
        plan=PlanRequest(),
        export=ExportRequest(variant="pending"),
    )

    result = Readio().run(source, request)

    assert result.workspace == workspace
    assert result.variant == projected_variant
    assert result.render_plan.native == "projected"
    assert calls["render"] == projected_variant
    assert calls["compose"] == projected_variant
    assert calls["export"] == projected_variant


def test_public_render_intent_uses_the_canonical_request_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    intent = create_document_intent("article.md", profile="article.renderprofile.toml")
    progress = print
    request = object()
    marker = object()
    calls: dict[str, object] = {}
    service = Readio()

    def build(received_intent: object, **overrides: object) -> object:
        calls.update(intent=received_intent, overrides=overrides)
        return request

    def render(received_request: object, *, dry_run: bool, on_progress: object) -> object:
        calls.update(request=received_request, dry_run=dry_run, on_progress=on_progress)
        return marker

    monkeypatch.setattr(service, "request_from_intent", build)
    monkeypatch.setattr(service, "render", render)
    output = tmp_path / "article.mp3"

    result = service.render_intent(
        intent,
        dry_run=True,
        on_progress=progress,
        output=output,
    )

    assert result is marker
    assert calls == {
        "intent": intent,
        "overrides": {"output": output},
        "request": request,
        "dry_run": True,
        "on_progress": progress,
    }
