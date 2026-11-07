from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import audiocompose
import utterplan
import voicerender

from readio.api.types import ExportRequest, VoiceRenderRequest
from readio.integrations import audiocompose as audiocompose_integration
from readio.integrations import audioexport as audioexport_integration
from readio.integrations import utterplan as utterplan_integration
from readio.integrations import voicerender as voicerender_integration
from readio.workspace import WorkspaceRef


def test_utterplan_inspection_adapters_forward_specialist_options(
    tmp_path: Path, monkeypatch: Any
) -> None:
    workspace = WorkspaceRef(tmp_path / "book.ssmd-bundle", "book")
    config = object()
    profile = tmp_path / "voice.renderprofile.toml"
    compile_result = object()
    projection_result = object()
    captured: list[tuple[str, Path, dict[str, Any]]] = []

    def inspect_compile(path: Path, **kwargs: Any) -> object:
        captured.append(("compile", path, kwargs))
        return compile_result

    def inspect_projection(path: Path, **kwargs: Any) -> object:
        captured.append(("projection", path, kwargs))
        return projection_result

    monkeypatch.setattr(utterplan, "inspect_bundle_compile", inspect_compile)
    monkeypatch.setattr(utterplan, "inspect_bundle_projection", inspect_projection)

    assert (
        utterplan_integration.inspect_bundle_compile(
            workspace, chapters="2-4", language="en-US", planner_config=config
        )
        is compile_result
    )
    assert (
        utterplan_integration.inspect_bundle_projection(
            workspace,
            profile=profile,
            chapters="3-5",
            variant="narration",
            output_root=tmp_path / "renderplans",
        )
        is projection_result
    )
    assert captured == [
        (
            "compile",
            workspace.root,
            {"chapters": "2-4", "language": "en-US", "planner_config": config, "output_dir": None},
        ),
        (
            "projection",
            workspace.root,
            {
                "profile": profile,
                "chapters": "3-5",
                "variant": "narration",
                "pause_timing": None,
                "utterplan_dir": None,
                "output_root": tmp_path / "renderplans",
            },
        ),
    ]


def test_voicerender_and_audiocompose_memory_adapters_are_thin(
    tmp_path: Path, monkeypatch: Any
) -> None:
    workspace = WorkspaceRef(tmp_path / "story.ssmd-bundle", "document")
    request = VoiceRenderRequest(variant="narration", chapters="all", speech="selected")
    render_inspection = object()
    composition_inspection = object()
    memory_result = object()
    captured: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def inspect_render(*args: Any, **kwargs: Any) -> object:
        captured.append(("render-inspect", args, kwargs))
        return render_inspection

    def inspect_composition(*args: Any, **kwargs: Any) -> object:
        captured.append(("compose-inspect", args, kwargs))
        return composition_inspection

    def compose_memory(*args: Any, **kwargs: Any) -> object:
        captured.append(("compose-memory", args, kwargs))
        return memory_result

    monkeypatch.setattr(voicerender, "inspect_bundle_render", inspect_render)
    monkeypatch.setattr(audiocompose, "inspect_bundle_composition", inspect_composition)
    monkeypatch.setattr(audiocompose, "compose_renderplan_memory", compose_memory)

    assert voicerender_integration.inspect_bundle_render(workspace, request) is render_inspection
    assert (
        audiocompose_integration.inspect_bundle_composition(
            workspace, variant="narration", chapters="all"
        )
        is composition_inspection
    )
    assert (
        audiocompose_integration.compose_renderplan_memory("plan", {}, emphasis_policy="strict")
        is memory_result
    )
    assert captured == [
        (
            "render-inspect",
            (workspace.root,),
            {
                "variant": "narration",
                "chapters": "all",
                "speech": "selected",
                "renderplan_root": None,
                "output_root": None,
            },
        ),
        (
            "compose-inspect",
            (workspace.root,),
            {
                "variant": "narration",
                "chapters": "all",
                "renderplan_root": None,
                "voicerender_root": None,
                "output_root": None,
            },
        ),
        ("compose-memory", ("plan", {}), {"emphasis_policy": "strict"}),
    ]


def test_audioexport_inspection_adapter_uses_lazy_public_package_api(
    tmp_path: Path, monkeypatch: Any
) -> None:
    workspace = WorkspaceRef(tmp_path / "story.ssmd-bundle", "document")
    request = ExportRequest(variant="narration", output=tmp_path / "story.m4b", format="m4b")
    result = object()
    captured: dict[str, Any] = {}

    def inspect(path: Path, **kwargs: Any) -> object:
        captured.update(path=path, **kwargs)
        return result

    monkeypatch.setattr(
        audioexport_integration,
        "_audioexport_api",
        lambda: SimpleNamespace(inspect_bundle_export=inspect),
    )

    assert audioexport_integration.inspect_bundle_export(workspace, request) is result
    assert captured["path"] == workspace.root
    assert captured["variant"] == "narration"
    assert captured["output"] == tmp_path / "story.m4b"
    assert captured["format"] == "m4b"


def test_voicerender_in_memory_adapters_forward_read_only_parameters(
    monkeypatch: Any,
) -> None:
    preflight_result = object()
    render_result = object()
    captured: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def preflight(plan: Any, **kwargs: Any) -> object:
        captured.append(("preflight", (plan,), kwargs))
        return preflight_result

    def render_memory(plan: Any, **kwargs: Any) -> object:
        captured.append(("render", (plan,), kwargs))
        return render_result

    monkeypatch.setattr(voicerender, "preflight_render_plan", preflight)
    monkeypatch.setattr(voicerender, "render_renderplan_memory", render_memory)

    assert (
        voicerender_integration.preflight_render_plan(
            "plan", output=None, emphasis_policy="reject", max_open_sessions=2
        )
        is preflight_result
    )
    assert (
        voicerender_integration.render_renderplan_memory(
            "plan", preflight=preflight_result, speech_numbers={3}
        )
        is render_result
    )
    assert captured == [
        (
            "preflight",
            ("plan",),
            {"output": None, "emphasis_policy": "reject", "max_open_sessions": 2},
        ),
        ("render", ("plan",), {"preflight": preflight_result, "speech_numbers": {3}}),
    ]
