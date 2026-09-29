from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from readio.api import (
    AudiobookProjectChapter,
    AudiobookProjectDescription,
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
    SynthesisResolution,
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


def test_audiobook_and_synthesis_preflight_types_are_immutable_and_json_serializable(
    tmp_path: Path,
) -> None:
    project = ProjectRef(
        root=tmp_path / "book.readio",
        project_id="book-1",
        name="Book",
        kind="audiobook",
        source_format="epub",
    )
    chapter = AudiobookProjectChapter(
        number=3, scope_id="chapter-0003", title="Chapter Three", level=1
    )
    description = AudiobookProjectDescription(
        project=project,
        source=project.root / "source" / "book.epub",
        chapters=(chapter,),
    )
    resolution = SynthesisResolution(
        engine="pykokoro",
        language="en-us",
        voice="af_heart",
        model="kokoro-v1",
        model_source="github",
        quality="fp32",
        speed=1.0,
        unit="paragraph",
        pause_mode="auto",
        voice_level="off",
        provider="kokoro",
        lexicons=("lex-a", "lex-b"),
        g2p_fallback="espeak",
        lexicon_data_policy="installed-only",
        language_detection="auto",
        detect_languages=("en-us", "de"),
        allow_experimental=True,
    )

    description_payload = json.loads(json.dumps(description.to_dict()))
    resolution_payload = json.loads(json.dumps(resolution.to_dict()))
    assert description_payload["chapters"][0]["scope_id"] == "chapter-0003"
    assert resolution_payload["model_source"] == "github"
    assert resolution_payload["lexicons"] == ["lex-a", "lex-b"]
    assert resolution_payload["g2p_fallback"] == "espeak"
    assert resolution_payload["lexicon_data_policy"] == "installed-only"
    assert resolution_payload["language_detection"] == "auto"
    assert resolution_payload["detect_languages"] == ["en-us", "de"]
    assert resolution_payload["allow_experimental"] is True
    with pytest.raises(FrozenInstanceError):
        resolution.engine = "piper"  # type: ignore[misc]

    legacy_resolution = SynthesisResolution(
        engine="pykokoro",
        language="en-us",
        voice="af_heart",
        model="kokoro-v1",
        model_source="github",
        quality="fp32",
        speed=1.0,
        unit="paragraph",
        pause_mode="auto",
        voice_level="off",
    )
    assert legacy_resolution.lexicons is None
    assert legacy_resolution.g2p_fallback is None
    assert legacy_resolution.lexicon_data_policy is None
    assert legacy_resolution.language_detection is None
    assert legacy_resolution.detect_languages is None
    assert legacy_resolution.allow_experimental is False
    assert set(legacy_resolution.to_dict()) >= {
        "lexicons",
        "g2p_fallback",
        "lexicon_data_policy",
        "language_detection",
        "detect_languages",
        "allow_experimental",
    }

    legacy_positional = SynthesisResolution(
        "pykokoro",
        "en-us",
        "af_heart",
        "kokoro-v1",
        "github",
        "fp32",
        1.0,
        "paragraph",
        "auto",
        "off",
        None,
        None,
        None,
        (),
    )
    assert legacy_positional.diagnostics == ()
    assert legacy_positional.lexicons is None


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


def test_project_settings_types_are_immutable_and_copy_nested_engine_options() -> None:
    from readio.api.types import (
        ProjectSettings,
        ProjectSettingsPatch,
        ProjectSynthesisSettings,
        UNSET,
    )

    options = {"nested": {"flags": [True, None]}}
    synthesis = ProjectSynthesisSettings(engine_options=options)
    settings = ProjectSettings(synthesis=synthesis)
    patch = ProjectSettingsPatch()

    options["nested"]["flags"].append(False)
    assert synthesis.engine_options["nested"]["flags"] == (True, None)
    assert patch.synthesis is UNSET
    assert patch.composition is UNSET
    assert settings.synthesis is synthesis
    with pytest.raises(TypeError):
        synthesis.engine_options["nested"]["new"] = "value"  # type: ignore[index]
