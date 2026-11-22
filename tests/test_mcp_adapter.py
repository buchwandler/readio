from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from readio.api import (
    AudioBuffer,
    Readio,
    RenderResult,
    SourceCapabilities,
    SourceInspection,
    SpeakResult,
    WorkflowPlan,
    WorkflowStep,
)
from readio.mcp import server


def test_mcp_tools_publish_task_oriented_schemas() -> None:
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}

    assert set(tools) == {
        "speech_inspect_source",
        "speech_inspect_workflow",
        "speech_capabilities",
        "speech_list_engines",
        "speech_list_voices",
        "speech_render",
        "speech_speak",
    }
    render_schema = tools["speech_render"].inputSchema
    assert {"source", "profile", "output"} <= set(render_schema["required"])
    assert render_schema["properties"]["replace_output"]["default"] is False
    rebuild_schema = render_schema["properties"]["rebuild_from"]
    assert {"convert", "compile", "render_plan", "render", "compose", "export"} <= set(
        rebuild_schema["anyOf"][0]["enum"]
    )
    assert {"text", "engine", "target"} <= set(tools["speech_speak"].inputSchema["required"])


def test_source_inspection_is_read_only_and_uses_shared_serialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "not-created.epub"
    seen: list[Path] = []

    def source_capabilities(_self: Readio, value: Path) -> SourceCapabilities:
        seen.append(value)
        return SourceCapabilities(SourceInspection(value, "book"), True, False, "m4b")

    monkeypatch.setattr(Readio, "source_capabilities", source_capabilities)

    result = server.speech_inspect_source(str(source))

    assert result["schema"] == "readio.source-capabilities.v1"
    assert seen == [source]
    assert not source.exists()
    assert list(tmp_path.iterdir()) == []


def test_render_returns_blocked_plan_without_calling_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "book.epub"
    profile = tmp_path / "profile.toml"
    output = tmp_path / "book.m4b"
    blocked_plan = WorkflowPlan(
        source,
        None,
        "temporary",
        None,
        output,
        (
            WorkflowStep(
                "compile",
                "utterplan",
                "blocked",
                None,
                "Rebuild authorization is required.",
            ),
        ),
    )
    inspected: list[object] = []
    rendered: list[object] = []

    def inspect(_self: Readio, request: object) -> WorkflowPlan:
        inspected.append(request)
        return blocked_plan

    def render(_self: Readio, request: object, **kwargs: object) -> RenderResult:
        rendered.append((request, kwargs))
        raise AssertionError("blocked plans must not be rendered")

    monkeypatch.setattr(Readio, "inspect", inspect)
    monkeypatch.setattr(Readio, "render", render)

    result = server.speech_render(str(source), str(profile), str(output))

    assert result["schema"] == "readio.workflow-plan.v1"
    assert result["result"]["executable"] is False
    assert inspected[0].source == source
    assert inspected[0].profile == profile
    assert inspected[0].output == output
    assert inspected[0].rebuild_from is None
    assert inspected[0].replace_output is False
    assert rendered == []
    assert not output.exists()


def test_render_uses_explicit_authorization_and_only_readio_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "book.epub"
    profile = tmp_path / "profile.toml"
    output = tmp_path / "book.m4b"
    plan = WorkflowPlan(
        source,
        None,
        "temporary",
        None,
        output,
        (WorkflowStep("render", "voicerender", "replace", None, "Explicit rebuild."),),
    )
    inspected: list[object] = []
    rendered: list[tuple[object, object]] = []

    def inspect(_self: Readio, request: object) -> WorkflowPlan:
        inspected.append(request)
        return plan

    def render(_self: Readio, request: object, *, include_report: bool = False) -> RenderResult:
        rendered.append((request, include_report))
        return RenderResult(output, None, "temporary", "voice-profile", (), ())

    monkeypatch.setattr(Readio, "inspect", inspect)
    monkeypatch.setattr(Readio, "render", render)

    result = server.speech_render(
        str(source),
        str(profile),
        str(output),
        rebuild_from="render",
        replace_output=True,
        include_report=True,
    )

    assert result["schema"] == "readio.render-result.v1"
    assert inspected[0].rebuild_from == "render"
    assert inspected[0].replace_output is True
    assert rendered == [(inspected[0], True)]


def test_speak_returns_shared_versioned_audio_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = SpeakResult(AudioBuffer([0.0, 0.5], 22050), "profile")

    def synthesize(_self: Readio, request: object) -> SpeakResult:
        assert request.text == "Hello"
        assert request.engine == "engine"
        assert request.target == "target"
        return result

    monkeypatch.setattr(Readio, "synthesize", synthesize)

    value = server.speech_speak("Hello", "engine", "target")

    assert value["schema"] == "readio.speak-result.v1"
    assert value["result"]["audio"]["media_type"] == "audio/wav"
