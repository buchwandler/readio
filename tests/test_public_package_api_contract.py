from __future__ import annotations

from importlib import import_module
from typing import Any

import pytest

CORE_PUBLIC_APIS = {
    "ssmdconvert": ("convert_bundle", "load_bundle_workspace"),
    "utterplan": ("compile_bundle", "render_bundle"),
    "voicerender": (
        "render_bundle",
        "list_engines",
        "engine_status",
        "engine_options",
        "discover_targets",
        "list_models",
        "model_status",
        "install_model",
        "verify_model",
        "remove_model",
        "list_voices",
        "inspect_voice",
        "resolve_voice",
        "parse_voice_selector",
        "VoiceCloneRequest",
        "clone_voice_profile",
        "OnnxRuntimeStatus",
        "onnx_runtime_status",
        "set_onnx_runtime_providers",
        "clear_onnx_runtime_providers",
        "create_voice_source",
        "load_voice_source",
        "save_voice_source",
        "validate_voice_source",
        "create_render_profile",
        "load_render_profile",
        "save_render_profile",
        "validate_render_profile",
        "refresh_render_profile",
        "list_profile_roles",
        "set_profile_role",
        "remove_profile_role",
        "set_default_profile_role",
    ),
    "audiocompose": ("compose_bundle", "BundleComposeOptions"),
}


@pytest.mark.parametrize(("package", "symbols"), CORE_PUBLIC_APIS.items())
def test_core_specialist_public_apis_are_importable(package: str, symbols: tuple[str, ...]) -> None:
    module: Any = import_module(package)
    missing = [symbol for symbol in symbols if not callable(getattr(module, symbol, None))]
    assert not missing, f"{package} is missing public root APIs: {', '.join(missing)}"


def test_audioexport_core_public_api_is_importable() -> None:
    import audioexport as module

    assert callable(getattr(module, "export_bundle", None))


def test_readio_public_api_centers_on_unified_workflows() -> None:
    from readio import api

    for symbol in (
        "WorkflowPlan",
        "WorkflowStep",
        "WorkflowEvent",
        "WorkflowState",
        "WorkspaceIntent",
        "runtime_status",
        "set_runtime_providers",
        "reset_runtime_providers",
    ):
        assert hasattr(api, symbol), f"readio.api is missing {symbol}"

    for stage_type in (
        "BundleStatus",
        "ComposeRequest",
        "CreateWorkspaceRequest",
        "ExportRequest",
        "PlanRequest",
        "PipelineEvent",
        "PipelineResult",
        "PipelineStage",
        "RenderExecutionOptions",
        "RenderIntentPlan",
        "RenderIntentResult",
        "RenderPlanRequest",
        "RenderPlanResult",
        "ResolvedRenderIntent",
        "RunRequest",
        "StatusRequest",
        "VoiceRenderRequest",
    ):
        assert not hasattr(api, stage_type), (
            f"stage-oriented type {stage_type} leaked from readio.api"
        )

    assert callable(api.Readio.speak)
    assert callable(api.Readio.render)
    assert callable(api.Readio.inspect)


def test_readio_render_intent_api_is_public() -> None:
    from readio import api

    for symbol in (
        "INTENT_SCHEMA",
        "InputIntent",
        "WorkspaceIntent",
        "RenderIntent",
        "create_book_intent",
        "create_document_intent",
        "load_render_intent",
        "save_render_intent",
        "parse_render_intent",
    ):
        assert hasattr(api, symbol), f"readio.api is missing {symbol}"


def test_readio_source_aware_profile_api_is_public() -> None:
    from readio import api

    for symbol in (
        "DocumentVoiceRequirements",
        "DocumentVoiceRole",
        "RoleAlias",
        "RoleVoiceBinding",
        "create_document_profile",
        "inspect_document_voice_requirements",
    ):
        assert hasattr(api, symbol), f"readio.api is missing {symbol}"
    assert callable(api.create_document_profile)
    assert callable(api.inspect_document_voice_requirements)


def test_readio_workflow_foundation_types_are_public() -> None:
    from readio import api

    for symbol in (
        "WorkspaceMode",
        "WorkspacePolicy",
        "PlanningOptions",
        "VoiceRenderOptions",
        "CompositionOptions",
        "ExportOptions",
        "SpeakRequest",
        "ReadioError",
        "ReadioStageError",
        "RenderRequest",
        "RenderResult",
        "OutputCollisionError",
        "WorkflowAction",
        "WorkflowError",
        "WorkflowReplanError",
        "WorkflowResolutionError",
        "WorkflowStageError",
        "WorkflowStateError",
        "WorkflowEvent",
        "WorkflowInspectionState",
        "WorkflowPlan",
        "WorkflowStage",
        "WorkflowState",
        "WorkflowStep",
    ):
        assert hasattr(api, symbol), f"readio.api is missing {symbol}"
