from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from readio.api import (
    ComposeRequest,
    ExportRequest,
    PlanRequest,
    Readio,
    RenderExecutionOptions,
    RenderPlanRequest,
    RenderPlanResult,
    RunRequest,
    VoiceRenderRequest,
    WorkspaceRef,
    create_document_intent,
)
from readio.api import service as service_module


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


def test_public_render_intent_api_delegates_options_and_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    intent = create_document_intent("article.md", profile="article.renderprofile.toml")
    options = RenderExecutionOptions(force_stages=("render",))
    progress = print
    marker = object()
    calls: dict[str, object] = {}

    def execute(
        service: Readio,
        received_intent: object,
        received_options: RenderExecutionOptions | None,
        *,
        on_progress: object,
        **overrides: object,
    ) -> object:
        calls.update(
            service=service,
            intent=received_intent,
            options=received_options,
            on_progress=on_progress,
            overrides=overrides,
        )
        return marker

    monkeypatch.setattr(service_module, "_render_intent", execute)
    service = Readio()
    result = service.render_intent(
        intent,
        options,
        on_progress=progress,
        output=tmp_path / "article.mp3",
    )

    assert result is marker
    assert calls["service"] is service
    assert calls["intent"] is intent
    assert calls["options"] is options
    assert calls["on_progress"] is progress
    assert calls["overrides"] == {"output": tmp_path / "article.mp3"}
