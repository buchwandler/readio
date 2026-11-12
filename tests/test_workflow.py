from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from readio.api.types import PipelineEvent, WorkflowEvent
from readio.errors import (
    OutputCollisionError,
    ReadioStageError,
    WorkflowError,
    WorkflowReplanError,
    WorkflowResolutionError,
    WorkflowStageError,
    WorkflowStateError,
)
from readio.progress import normalize_workflow_progress
from readio.workflow import make_workflow_plan, normalize_inspection_state, plan_workflow_step
from readio.workspace import WorkspacePolicy


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("reusable", "complete"),
        ("match", "complete"),
        ("not_rendered", "not_started"),
        ("profile_mismatch", "blocked"),
        ("corrupt", "error"),
        ("not-known", "unknown"),
    ],
)
def test_normalize_specialist_inspection_states(raw: str, normalized: str) -> None:
    assert normalize_inspection_state({"state": raw}) == normalized


def test_workflow_step_reuses_complete_and_preserves_specialist_selector() -> None:
    report = SimpleNamespace(
        state="complete",
        recommended_selector="chapter-a,chapter-c",
        details={"selected_count": 2},
    )

    step = plan_workflow_step("render", "voicerender", report, selector="all")

    assert step.action == "reuse"
    assert step.selector == "chapter-a,chapter-c"
    assert step.force is False
    assert step.details == {"selected_count": 2}


def test_explicit_rebuild_of_complete_stage_requires_replacement_safety() -> None:
    inspection = {"state": "complete"}

    blocked = plan_workflow_step("render", "voicerender", inspection, explicit_rebuild=True)
    replace = plan_workflow_step(
        "render", "voicerender", inspection, explicit_rebuild=True, replacement_safe=True
    )

    assert blocked.action == "blocked"
    assert replace.action == "replace"
    assert replace.force is True


def test_workflow_step_executes_missing_but_fails_closed_for_unknown() -> None:
    missing = plan_workflow_step("compile", "utterplan", {"state": "not_started"})
    unknown = plan_workflow_step("compile", "utterplan", {})

    assert missing.action == "execute"
    assert unknown.action == "blocked"
    assert unknown.state == "unknown"


def test_partial_inspection_with_stale_components_requires_safe_rebuild() -> None:
    report = {"state": "partial", "force_required": True, "recommended_selector": "chapter-2"}

    blocked = plan_workflow_step("compile", "utterplan", report)
    replace = plan_workflow_step(
        "compile", "utterplan", report, explicit_rebuild=True, replacement_safe=True
    )

    assert blocked.action == "blocked"
    assert replace.action == "replace"
    assert replace.force is True
    assert replace.selector == "chapter-2"


@pytest.mark.parametrize(
    ("explicit_rebuild", "replacement_safe", "action"),
    [
        (False, False, "blocked"),
        (True, False, "blocked"),
        (False, True, "blocked"),
        (True, True, "replace"),
    ],
)
def test_stale_replacement_requires_explicit_request_and_specialist_safety(
    explicit_rebuild: bool, replacement_safe: bool, action: str
) -> None:
    step = plan_workflow_step(
        "render",
        "voicerender",
        {"state": "stale", "reason": "profile identity changed"},
        explicit_rebuild=explicit_rebuild,
        replacement_safe=replacement_safe,
    )

    assert step.action == action
    assert step.force is (action == "replace")


def test_make_workflow_plan_carries_workspace_policy_and_paths(tmp_path: Path) -> None:
    source = tmp_path / "story.txt"
    root = tmp_path / "story.ssmd-bundle"
    step = plan_workflow_step("compile", "utterplan", {"state": "not_started"})
    policy = WorkspacePolicy(
        mode="persistent",
        root=root,
        source_is_bundle=False,
        newly_created=True,
        cleanup=False,
    )

    plan = make_workflow_plan(
        source,
        policy,
        variant="narration",
        output=tmp_path / "out.wav",
        steps=(step,),
    )

    assert plan.source == source.resolve()
    assert plan.workspace == root
    assert plan.workspace_mode == "persistent"
    assert plan.variant == "narration"
    assert plan.output == (tmp_path / "out.wav").absolute()
    assert plan.steps == (step,)


def test_normalize_workflow_progress_preserves_source_details() -> None:
    seen: list[WorkflowEvent] = []
    callback = normalize_workflow_progress(
        "render", "voicerender", seen.append, selector="chapter-4"
    )
    assert callback is not None
    callback(
        SimpleNamespace(
            kind="speech.completed",
            completed=2,
            total=5,
            message="rendered occurrence",
            elapsed_seconds=1.25,
            details={"chapter_id": "chapter-4", "number": 7},
            engine="local",
        )
    )

    event = seen[0]
    assert event.stage == "render"
    assert event.package == "voicerender"
    assert event.selector == "chapter-4"
    assert event.kind == "speech.completed"
    assert (event.current, event.total) == (2, 5)
    assert event.message == "rendered occurrence"
    assert event.elapsed_seconds == 1.25
    assert event.details == {
        "engine": "local",
        "chapter_id": "chapter-4",
        "number": 7,
        "chapter_number": 7,
    }


def test_normalize_workflow_progress_unwraps_pipeline_event_source() -> None:
    raw_event = SimpleNamespace(
        kind="speech.completed",
        current=9,
        total=12,
        message="nested message",
        phase="nested phase",
        elapsed_seconds=1.25,
        chapter_id="c7",
        number=7,
        title="The Long Way Down",
        engine="local",
    )
    seen: list[WorkflowEvent] = []
    callback = normalize_workflow_progress("render", "VoiceRender", seen.append, selector="5-17")
    assert callback is not None
    callback(
        PipelineEvent(
            stage="render",
            current=2,
            total=5,
            message="segment rendered",
            source=raw_event,
        )
    )

    event = seen[0]
    assert (event.current, event.total) == (2, 5)
    assert event.message == "segment rendered"
    assert event.kind == "speech.completed"
    assert event.elapsed_seconds == 1.25
    assert event.selector == "5-17"
    assert event.details == {
        "chapter_id": "c7",
        "number": 7,
        "title": "The Long Way Down",
        "engine": "local",
        "chapter_number": 7,
        "chapter_title": "The Long Way Down",
    }
    assert "source" not in event.details


def test_normalize_workflow_progress_does_not_map_segment_number_to_chapter() -> None:
    seen: list[WorkflowEvent] = []
    callback = normalize_workflow_progress("render", "VoiceRender", seen.append)
    assert callback is not None
    callback(SimpleNamespace(number=51, segment_id="s51"))

    assert seen[0].details["number"] == 51
    assert "chapter_number" not in seen[0].details


def test_normalize_workflow_progress_uses_nested_phase_as_message_fallback() -> None:
    seen: list[WorkflowEvent] = []
    callback = normalize_workflow_progress("render", "VoiceRender", seen.append)
    assert callback is not None
    callback(SimpleNamespace(message=None, source=SimpleNamespace(phase="chapter rendered")))

    assert seen[0].message == "chapter rendered"


def test_normalize_workflow_progress_accepts_structured_mapping_events() -> None:
    seen: list[WorkflowEvent] = []
    callback = normalize_workflow_progress("compose", "audiocompose", seen.append)
    assert callback is not None
    callback({"kind": "job.completed", "current": 0, "total": 0, "job_id": "job-1"})

    assert seen[0].current == 0
    assert seen[0].total == 0
    assert seen[0].details["job_id"] == "job-1"
    assert normalize_workflow_progress("compose", "audiocompose", None) is None


def test_normalize_workflow_progress_preserves_slotted_specialist_fields() -> None:
    @dataclass(slots=True)
    class SpecialistEvent:
        kind: str
        current: int
        total: int
        message: str
        voice_id: str

    seen: list[WorkflowEvent] = []
    callback = normalize_workflow_progress("render", "voicerender", seen.append)
    assert callback is not None
    callback(SpecialistEvent("speech.done", 1, 1, "complete", "voice-1"))

    assert seen[0].details == {"voice_id": "voice-1"}


def test_workflow_errors_retain_specialist_cause_and_context(tmp_path: Path) -> None:
    source = tmp_path / "story.txt"
    workspace = tmp_path / "story.ssmd-bundle"
    cause = ValueError("invalid RenderProfile")
    error = WorkflowStageError(
        "compile",
        str(cause),
        package="utterplan",
        source=source,
        workspace=workspace,
        variant="narration",
        selector="chapter-2",
        code="profile.invalid",
        cause=cause,
    )
    legacy = ReadioStageError("compile", "invalid bundle", workspace=workspace, cause=cause)

    assert isinstance(error, WorkflowError)
    assert error.cause is cause
    assert error.__cause__ is None
    assert error.stage == "compile"
    assert error.package == "utterplan"
    assert error.source == source
    assert error.workspace == workspace
    assert error.variant == "narration"
    assert error.selector == "chapter-2"
    assert error.code == "profile.invalid"
    assert "utterplan compile for chapter-2 (narration)" in str(error)
    assert str(legacy) == f"compile failed for {workspace}: invalid bundle"
    assert isinstance(WorkflowResolutionError("bad source"), WorkflowError)
    assert isinstance(WorkflowStateError("unknown state"), WorkflowError)
    assert isinstance(WorkflowReplanError("no progress"), WorkflowError)
    assert isinstance(OutputCollisionError("output exists"), WorkflowError)
