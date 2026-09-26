from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from readio.api import (
    CompositionOptions,
    Diagnostic,
    DiscoveryOptions,
    Document,
    ExportOptions,
    InputRequest,
    PlanRequest,
    ProjectBuildRequest,
    ProjectBuildResult,
    ProjectRef,
    ProjectStatus,
    ReadioEvent,
    RenderResult,
    RenderSummary,
    ResolvedPlan,
    StageOperation,
    StageStatus,
    SynthesisRequest,
    document_from_file,
    document_from_text,
)


def test_public_request_defaults_and_document_helpers(tmp_path: Path) -> None:
    document = document_from_text("hello", input_format="markdown")
    assert isinstance(document, Document)
    assert document.format == "markdown"

    source = tmp_path / "input.ssmd"
    source.write_text("Hello", encoding="utf-8")
    from_file = document_from_file(source)
    assert from_file.text == "Hello"
    assert from_file.source_path == source
    assert from_file.format == "ssmd"

    request = PlanRequest(
        operation="render",
        input=InputRequest(document=document),
        composition=CompositionOptions(target_lufs=-18.0),
    )
    assert request.synthesis == SynthesisRequest()
    assert request.composition.target_lufs == -18.0
    assert DiscoveryOptions().preference == "auto"
    assert ExportOptions().format == "wav"
    assert ProjectBuildRequest().target == "export"


def test_synthesis_engine_options_are_json_compatible_and_frozen() -> None:
    request = SynthesisRequest(
        engine="piper",
        engine_options={"length_scale": 1.1, "flags": [True, None]},
    )
    assert request.engine_options["length_scale"] == 1.1
    with pytest.raises(FrozenInstanceError):
        request.engine = "pykokoro"  # type: ignore[misc]


def test_project_and_render_results_serialize_paths_and_nested_models(tmp_path: Path) -> None:
    project = ProjectRef(
        root=tmp_path / "project",
        project_id="project-1",
        name="Episode",
        kind="document",
        source_format="ssmd",
    )
    status = ProjectStatus(
        project=project,
        stages=(StageStatus(stage="plan", state="current", reason="plan matches"),),
        issues=(
            Diagnostic(
                code="input.warning",
                severity="warning",
                message="Example",
                source_path=tmp_path / "source.ssmd",
                details={"line": 3},
            ),
        ),
        next_actions=(),
    )
    build = ProjectBuildResult(
        project=project,
        operations=(StageOperation(stage="synthesis", action="reused", details={"count": 2}),),
        output_path=tmp_path / "out.wav",
    )
    render = RenderResult(
        plan=ResolvedPlan(),
        summary=RenderSummary(sample_rate=24000, sample_count=100),
        output_path=tmp_path / "render.wav",
    )

    for result in (status, build, render):
        payload = result.to_dict()
        json.loads(json.dumps(payload))

    assert status.stage("plan").state == "current"
    assert status.to_dict()["project"]["root"] == project.root.as_posix()
    assert status.to_dict()["issues"][0]["source_path"] == (tmp_path / "source.ssmd").as_posix()
    assert build.to_dict()["output_path"] == (tmp_path / "out.wav").as_posix()
    assert render.to_dict()["summary"]["sample_count"] == 100
    assert render.to_dict()["output_path"] == (tmp_path / "render.wav").as_posix()


def test_public_event_fields_are_stable_and_frozen() -> None:
    event = ReadioEvent(kind="progress", operation="render", completed=1, total=2)
    assert event.kind == "progress"
    with pytest.raises(FrozenInstanceError):
        event.kind = "changed"  # type: ignore[misc]
