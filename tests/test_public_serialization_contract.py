from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest

from readio.api import (
    ErrorInfo,
    RenderResult,
    WorkflowAction,
    WorkflowArtifactRef,
    WorkflowBlocker,
    WorkflowPlan,
    WorkflowReasonCode,
    WorkflowStep,
    describe_error,
)
from readio.api.serialization import (
    error_info_to_dict,
    render_result_to_dict,
    workflow_plan_to_dict,
)
from readio.errors import (
    OutputCollisionError,
    WorkflowCancelledError,
    WorkflowError,
    WorkflowReplanError,
    WorkflowResolutionError,
    WorkflowStageError,
    WorkflowStateError,
)
from readio.workflow import make_workflow_plan, plan_workflow_step
from readio.workspace import WorkspacePolicy

REASON_CODES = {
    "artifact_missing",
    "artifact_partial",
    "artifact_current",
    "artifact_stale",
    "explicit_rebuild",
    "inspection_deferred",
    "dependency_not_ready",
    "specialist_blocked",
    "specialist_error",
    "specialist_state_unknown",
    "replacement_not_authorized",
    "replacement_not_safe",
    "output_replacement_required",
}


@pytest.mark.parametrize(
    ("stage", "inspection", "explicit_rebuild", "replacement_safe", "action", "code", "blocker"),
    [
        ("compile", {"state": "not_started"}, False, False, "execute", "artifact_missing", None),
        ("compile", {"state": "partial"}, False, False, "execute", "artifact_partial", None),
        (
            "compile",
            {"state": "complete", "reason": "mentions stale in a message"},
            False,
            False,
            "reuse",
            "artifact_current",
            None,
        ),
        (
            "compile",
            {"state": "stale"},
            False,
            True,
            "blocked",
            "replacement_not_authorized",
            "rebuild_required",
        ),
        ("compile", {"state": "stale"}, True, True, "replace", "explicit_rebuild", None),
        (
            "compile",
            {"state": "stale"},
            True,
            False,
            "blocked",
            "replacement_not_safe",
            "replacement_not_safe",
        ),
        (
            "compile",
            {"state": "blocked"},
            False,
            False,
            "blocked",
            "specialist_blocked",
            "specialist_unavailable",
        ),
        (
            "compile",
            {"state": "error"},
            False,
            False,
            "blocked",
            "specialist_error",
            "specialist_error",
        ),
        (
            "compile",
            {},
            False,
            False,
            "blocked",
            "specialist_state_unknown",
            "unknown_specialist_state",
        ),
        (
            "compile",
            {"state": "blocked", "dependency_not_ready": True},
            False,
            False,
            "blocked",
            "dependency_not_ready",
            "dependency_not_ready",
        ),
        (
            "compile",
            {"state": "not_started", "inspection_deferred": True},
            False,
            False,
            "execute",
            "inspection_deferred",
            None,
        ),
        (
            "export",
            {"state": "stale"},
            False,
            True,
            "blocked",
            "output_replacement_required",
            "output_replace_required",
        ),
        ("export", {"state": "stale"}, True, True, "replace", "explicit_rebuild", None),
    ],
)
def test_workflow_reason_and_blocker_contract(
    stage: str,
    inspection: object,
    explicit_rebuild: bool,
    replacement_safe: bool,
    action: WorkflowAction,
    code: str,
    blocker: str | None,
) -> None:
    step = plan_workflow_step(
        stage,  # type: ignore[arg-type]
        "audioexport" if stage == "export" else "utterplan",
        inspection,
        explicit_rebuild=explicit_rebuild,
        replacement_safe=replacement_safe,
    )

    assert step.action == action
    assert step.reason_code == code
    assert step.reason
    assert tuple(item.code for item in step.blockers) == (() if blocker is None else (blocker,))


def test_reason_code_set_and_logical_artifact_references_are_stable(tmp_path: Path) -> None:
    assert set(get_args(WorkflowReasonCode)) == REASON_CODES
    stages = ("convert", "compile", "render_plan", "render", "compose", "export")
    steps = tuple(plan_workflow_step(stage, "readio", {"state": "complete"}) for stage in stages)
    policy = WorkspacePolicy(
        mode="persistent",
        root=tmp_path / "book.ssmd-bundle",
        source_is_bundle=False,
        newly_created=False,
        cleanup=False,
    )
    plan = make_workflow_plan(
        tmp_path / "book.epub",
        policy,
        variant="voice-profile",
        output=tmp_path / "book.m4b",
        steps=steps,
    )

    assert plan.executable
    assert plan.schema_version == 1
    assert [step.outputs[0].kind for step in plan.steps] == [
        "workspace",
        "compiled_plan",
        "render_plan",
        "rendered_audio",
        "composed_audio",
        "final_audio",
    ]
    assert all(isinstance(ref, WorkflowArtifactRef) for step in plan.steps for ref in step.inputs)
    assert all(step.inputs or step.stage == "convert" for step in plan.steps)
    assert plan.steps[-1].outputs[0].path == plan.output
    assert all(
        ref.path is None
        for step in plan.steps
        for ref in step.outputs
        if ref.kind not in {"workspace", "final_audio"}
    )


def test_blocked_plan_is_not_executable_and_legacy_constructors_remain_valid(
    tmp_path: Path,
) -> None:
    step = WorkflowStep("compile", "utterplan", "blocked", None, "blocked")
    plan = WorkflowPlan(tmp_path / "source.md", None, "temporary", None, None, (step,))
    result = RenderResult(tmp_path / "output.mp3", None, "temporary", "variant", (), ())
    info = ErrorInfo("WorkflowError", "failure")

    assert not plan.executable
    assert step.reason_code == "specialist_state_unknown"
    assert step.inputs == () and step.outputs == () and step.blockers == ()
    assert plan.schema_version == 1
    assert result.warnings == ()
    assert info.recoverable is False and info.suggested_action is None


def test_workflow_error_codes_are_readio_owned_and_not_message_derived() -> None:
    errors: tuple[tuple[WorkflowError, str], ...] = (
        (WorkflowResolutionError("unsupported output format"), "invalid_request"),
        (WorkflowStateError("retry this later"), "unsafe_workflow_state"),
        (WorkflowReplanError("no progress"), "workflow_replan_failed"),
        (OutputCollisionError("output exists"), "output_exists"),
        (WorkflowCancelledError(), "cancellation_requested"),
        (WorkflowStageError("render", "specialist failure", cause=RuntimeError()), "stage_failed"),
    )
    for error, code in errors:
        assert error.code == code

    collision = describe_error(OutputCollisionError("target exists"))
    cancellation = describe_error(WorkflowCancelledError(stage="compose"))
    assert collision.recoverable is True
    assert collision.suggested_action == "set_replace_output"
    assert cancellation.code == "cancellation_requested"
    assert cancellation.stage == "compose"
    assert cancellation.recoverable is True
    assert cancellation.suggested_action == "inspect_then_retry"


def test_versioned_serializers_preserve_explicit_contract_shapes(tmp_path: Path) -> None:
    blocker = WorkflowBlocker(
        "rebuild_required", "Rebuild authorization is required.", owner="utterplan"
    )
    step = WorkflowStep(
        "compile",
        "utterplan",
        "blocked",
        "all",
        "The plan is stale.",
        reason_code="replacement_not_authorized",
        blockers=(blocker,),
    )
    plan = WorkflowPlan(tmp_path / "source.md", None, "temporary", None, None, (step,))
    render = RenderResult(tmp_path / "output.mp3", None, "temporary", "v1", (), ())
    info = ErrorInfo("WorkflowError", "Failure", code="unsafe_workflow_state")

    plan_json = workflow_plan_to_dict(plan)
    result_json = render_result_to_dict(render)
    error_json = error_info_to_dict(info)
    json.dumps((plan_json, result_json, error_json))

    assert plan_json["schema"] == "readio.workflow-plan.v1"
    assert plan_json["result"]["executable"] is False
    assert plan_json["result"]["steps"][0]["blockers"][0]["code"] == "rebuild_required"
    assert result_json["schema"] == "readio.render-result.v1"
    assert result_json["result"]["executed_stages"] == []
    assert error_json["schema"] == "readio.error-info.v1"
    assert error_json["result"]["recoverable"] is False


def test_specialist_error_codes_remain_nested_in_readio_stage_errors() -> None:
    error = WorkflowStageError(
        "render",
        "specialist failed",
        package="voicerender",
        code=None,
        cause=RuntimeError("failure"),
        details={"specialist_code": "voice.render.failed"},
    )
    info = describe_error(error)

    assert info.code == "stage_failed"
    assert info.details["specialist_code"] == "voice.render.failed"


def test_speak_result_uses_shared_versioned_audio_serializer() -> None:
    from base64 import b64decode

    from readio.api import AudioBuffer, SpeakResult, serialize_public
    from readio.api.serialization import speak_result_to_dict

    result = SpeakResult(AudioBuffer([0.0, 0.5], 22050), "profile")
    value = speak_result_to_dict(result)
    audio = value["result"]["audio"]

    json.dumps(value)
    assert value["schema"] == "readio.speak-result.v1"
    assert audio["media_type"] == "audio/wav"
    assert b64decode(audio["data"]).startswith(b"RIFF")
    assert serialize_public(result) == value


def test_engine_model_inventory_uses_normalized_versioned_schema() -> None:
    from readio.api import (
        EngineModelInfo,
        engine_model_to_dict,
        engine_models_to_dict,
        serialize_public,
    )

    model = EngineModelInfo(
        engine="kitten",
        target="kitten-v1",
        quality="high",
        language="en-US",
        label="Kitten v1",
        installed=True,
        available=True,
        state="ready",
        local_version="revision-7",
        available_version=None,
        update_state="unknown",
        bytes_installed=0,
        bytes_download=64,
        storage_id="kitten:bundle-1",
        installable=False,
        updatable=False,
        removable=True,
        verifiable=True,
    )
    row = engine_model_to_dict(model)
    payload = engine_models_to_dict((model,))

    assert payload == {
        "schema": "readio.engine-models.v1",
        "result": {"models": [row]},
    }
    assert serialize_public((model,)) == payload
    assert serialize_public(model) == payload
    assert engine_models_to_dict(()) == {
        "schema": "readio.engine-models.v1",
        "result": {"models": []},
    }
    assert "specialist" not in row
    json.dumps(payload)
