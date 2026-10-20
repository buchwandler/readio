from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from readio.api import (
    BookExportRequest,
    ComposeRequest,
    DocumentExportRequest,
    PlanRequest,
    Readio,
    RenderPlanRequest,
    RenderPlanResult,
    RunRequest,
    VoiceRenderRequest,
    WorkspaceRef,
)
from readio.api import service as service_module


@pytest.mark.parametrize("kind", ["document", "book"])
def test_run_propagates_exact_projected_variant_to_every_downstream_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["document", "book"],
) -> None:
    workspace = WorkspaceRef(
        tmp_path / ("book.ssmdbook" if kind == "book" else "document.ssmdoc"), kind
    )
    source = tmp_path / "input"
    projected_variant = "VoiceRender-defined/variant.with punctuation"
    calls: dict[str, object] = {}

    monkeypatch.setattr(service_module, "_create_workspace", lambda *_args, **_kwargs: workspace)

    def compile_workspace(
        received_workspace: WorkspaceRef, request: PlanRequest, **_kwargs: object
    ) -> str:
        calls["compile"] = (received_workspace, request)
        return "compiled"

    def project_workspace(
        received_workspace: WorkspaceRef,
        request: RenderPlanRequest,
        **_kwargs: object,
    ) -> RenderPlanResult:
        calls["projection"] = (received_workspace, request)
        return RenderPlanResult(native="projected", variant=projected_variant)

    def render_workspace(
        received_workspace: WorkspaceRef, request: VoiceRenderRequest, **_kwargs: object
    ) -> str:
        calls["render"] = request.variant
        return "rendered"

    def compose_workspace(
        received_workspace: WorkspaceRef, request: ComposeRequest, **_kwargs: object
    ) -> str:
        calls["compose"] = request.variant
        return "composed"

    monkeypatch.setattr(service_module, "plan", compile_workspace)
    monkeypatch.setattr(service_module, "render_plan", project_workspace)
    monkeypatch.setattr(service_module, "render", render_workspace)
    monkeypatch.setattr(service_module, "compose", compose_workspace)

    if kind == "book":
        export_request: DocumentExportRequest | BookExportRequest = BookExportRequest(
            variant="pending"
        )
    else:
        export_request = DocumentExportRequest(variant="pending")

    def export_workspace(
        received_workspace: WorkspaceRef,
        request: DocumentExportRequest | BookExportRequest,
    ) -> str:
        calls["export"] = request.variant
        return "exported"

    monkeypatch.setattr(service_module, "export", export_workspace)

    request = RunRequest(
        render_plan=RenderPlanRequest(profile=tmp_path / "profile.yaml", variant="display-label"),
        render=VoiceRenderRequest(variant="ignored"),
        compose=ComposeRequest(variant="ignored"),
        plan=PlanRequest(),
        export=export_request,
    )
    result = Readio().run(source, request)

    assert result.workspace == workspace
    assert result.variant == projected_variant
    assert result.render_plan.native == "projected"
    assert calls["render"] == projected_variant
    assert calls["compose"] == projected_variant
    assert calls["export"] == projected_variant
