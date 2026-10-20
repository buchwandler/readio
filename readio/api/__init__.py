"""Public Readio orchestration and VoiceRender management facade."""

from __future__ import annotations

from voicerender import VoiceCloneRequest  # type: ignore[import-not-found]

from readio.engines import (
    discover_engine_targets,
    inspect_engine,
    inspect_engine_model,
    install_engine_model,
    list_engine_models,
    list_engine_options,
    list_engines,
    remove_engine_model,
    verify_engine_model,
)
from readio.profiles import (
    create_profile,
    list_profile_roles,
    load_profile,
    refresh_profile,
    remove_profile_role,
    set_default_profile_role,
    set_profile_role,
    validate_profile,
)
from readio.speech import VoiceSelector, parse_voice_selector, speak
from readio.voices import (
    clone_voice,
    create_voice_source,
    inspect_voice,
    list_voices,
    show_voice_source,
    verify_voice_source,
)
from readio.workspace import CreateWorkspaceRequest, WorkspaceRef

from .service import Readio
from .types import (
    BookExportRequest,
    ComposeRequest,
    DocumentExportRequest,
    PipelineEvent,
    PipelineResult,
    PlanRequest,
    RenderPlanRequest,
    RenderPlanResult,
    RunRequest,
    VoiceRenderRequest,
)

__all__ = [
    "BookExportRequest",
    "ComposeRequest",
    "CreateWorkspaceRequest",
    "DocumentExportRequest",
    "PipelineEvent",
    "PipelineResult",
    "PlanRequest",
    "Readio",
    "RenderPlanRequest",
    "RenderPlanResult",
    "RunRequest",
    "VoiceCloneRequest",
    "VoiceRenderRequest",
    "VoiceSelector",
    "WorkspaceRef",
    "clone_voice",
    "create_profile",
    "create_voice_source",
    "discover_engine_targets",
    "inspect_engine",
    "inspect_engine_model",
    "inspect_voice",
    "install_engine_model",
    "list_engine_models",
    "list_engine_options",
    "list_engines",
    "list_profile_roles",
    "list_voices",
    "load_profile",
    "parse_voice_selector",
    "refresh_profile",
    "remove_engine_model",
    "remove_profile_role",
    "set_default_profile_role",
    "set_profile_role",
    "show_voice_source",
    "speak",
    "validate_profile",
    "verify_engine_model",
    "verify_voice_source",
]
