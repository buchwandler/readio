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
    assert callable(api.Readio.synthesize)
    assert callable(api.Readio.list_engines)
    assert callable(api.Readio.list_targets)
    assert callable(api.Readio.list_voices)
    assert callable(api.Readio.list_engine_options)
    assert callable(api.describe_error)
    assert hasattr(api, "EngineModelInfo")
    assert hasattr(api, "ModelState")
    assert hasattr(api, "ModelUpdateState")
    assert hasattr(api, "ModelOperationError")
    assert callable(api.engine_model_to_dict)
    assert callable(api.engine_models_to_dict)
    for method in (
        "list_models",
        "model_status",
        "install_model",
        "update_model",
        "verify_model",
        "remove_model",
    ):
        assert callable(getattr(api.Readio, method))
    assert callable(api.Readio.render)
    assert callable(api.Readio.inspect)
    assert callable(api.Readio.inspect_source)
    assert callable(api.Readio.inspect_intent)
    assert callable(api.Readio.request_from_intent)
    assert callable(api.Readio.plan_render_intent)
    assert callable(api.Readio.render_intent)
    assert callable(api.Readio.request_from_intent)
    assert callable(api.render_request_from_intent)

    assert callable(api.inspect_source)


def test_readio_render_intent_api_is_public() -> None:
    from readio import api

    for symbol in (
        "INTENT_SCHEMA",
        "InputIntent",
        "WorkspaceIntent",
        "RenderIntent",
        "PreparationIntent",
        "ProjectionIntent",
        "create_book_intent",
        "create_document_intent",
        "load_render_intent",
        "render_request_from_intent",
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
        "CancellationToken",
        "CatalogError",
        "EngineInfo",
        "EngineOptionInfo",
        "ErrorInfo",
        "TargetInfo",
        "VoiceInfo",
        "WorkflowCancelledError",
        "SpeakRequest",
        "SourceChapter",
        "SourceInspection",
        "ReadioError",
        "ReadioStageError",
        "RenderRequest",
        "RenderResult",
        "OutputCollisionError",
        "WorkflowAction",
        "WorkflowArtifactRef",
        "WorkflowBlocker",
        "WorkflowReasonCode",
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


def test_readio_api_control_types_do_not_leak_specialist_implementations() -> None:
    from readio import api

    assert api.PreparationOptions.__module__ == "readio.workspace"
    assert api.ProjectionOptions.__module__ == "readio.api.types"
    assert api.PreparationIntent.__module__ == "readio.intent"
    assert api.ProjectionIntent.__module__ == "readio.intent"
    for specialist_type in (
        "LinguisticsConfig",
        "PauseTimingConfig",
        "SpeechProfile",
        "SpacyLanguageModel",
    ):
        assert not hasattr(api, specialist_type), f"specialist type leaked: {specialist_type}"


def test_describe_error_projects_expected_workflow_context() -> None:
    from readio import api
    from readio.api.types import ErrorInfo
    from readio.errors import WorkflowStageError

    error = WorkflowStageError(
        "render",
        "engine failed",
        package="voicerender",
        selector="speech 2",
        code="engine.failed",
        cause=RuntimeError("engine failed"),
        details={"retryable": True},
    )

    info = api.describe_error(error)
    assert info == ErrorInfo(
        type="WorkflowStageError",
        message=str(error),
        code="engine.failed",
        stage="render",
        package="voicerender",
        selector="speech 2",
        details={"retryable": True},
    )
    with pytest.raises(TypeError, match="expects a ReadioError"):
        api.describe_error(RuntimeError("unexpected"))


def test_application_api_documentation_promotes_synthesis_and_catalog() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    api_docs = (root / "docs" / "api.md").read_text(encoding="utf-8")
    architecture_docs = (root / "docs" / "architecture.md").read_text(encoding="utf-8")
    cli_docs = (root / "docs" / "cli.md").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")

    for term in (
        "Readio.synthesize()",
        "to_wav_bytes()",
        "Readio.list_targets()",
        "Readio.list_engine_options()",
        "Readio.list_models()",
        "Engine model inventory and lifecycle",
        "readio.engine-models.v1",
        "CancellationToken",
        "describe_error()",
    ):
        assert term in api_docs
    assert "Readio.synthesize()" in readme
    assert "Readio.list_models()" in readme
    assert (
        "VoiceRender owns synthesis engine packages, model registries, model storage"
        in architecture_docs
    )
    assert "--normalized-json" in cli_docs
    assert "models update" in cli_docs


def test_readio_intent_schema_is_current_public_contract() -> None:
    from readio.api import INTENT_SCHEMA

    assert INTENT_SCHEMA == "readio.render.v3"


def test_versioned_workflow_serializers_are_public() -> None:
    from readio import api

    for name in (
        "workflow_plan_to_dict",
        "render_result_to_dict",
        "speak_result_to_dict",
        "error_info_to_dict",
        "serialize_public",
    ):
        assert callable(getattr(api, name))


def test_readio_capability_contract_is_public() -> None:
    from readio import api

    for name in (
        "CapabilityState",
        "SpecialistCapability",
        "WorkflowCapabilities",
        "ExportFormatCapability",
        "SourceCapabilities",
        "SynthesisCapabilities",
        "ReadioCapabilities",
        "ExecutionReport",
        "StageExecutionRecord",
        "capabilities_to_dict",
        "source_capabilities_to_dict",
        "synthesis_capabilities_to_dict",
        "export_capabilities_to_dict",
    ):
        assert hasattr(api, name), f"readio.api is missing {name}"

    for name in (
        "capabilities",
        "source_capabilities",
        "synthesis_capabilities",
        "export_capabilities",
    ):
        assert callable(getattr(api.Readio, name))
