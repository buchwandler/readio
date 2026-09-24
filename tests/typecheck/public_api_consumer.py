from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from readio.api import (
    UNSET,
    AudioSink,
    CatalogListing,
    ConfigurationInitResult,
    Diagnostic,
    DiscoveryOptions,
    DoctorReport,
    EventHandler,
    EventKind,
    EventStage,
    InputRequest,
    JsonValue,
    LanguageProfilePatch,
    LanguageProfileResolution,
    LanguageSettings,
    LexiconInfo,
    LexiconQuery,
    OutputRequest,
    PlannedOutputError,
    PlanNotExecutableError,
    PlanRequest,
    ProgressKind,
    ProjectBuildRequest,
    ProjectBuildResult,
    ProjectRef,
    ProjectRoleMutationResult,
    ProjectStatus,
    Readio,
    ReadioEvent,
    RenderResult,
    ResolvedPlan,
    SSMDCheckResult,
    SynthesisRequest,
    VoiceInfo,
    VoiceQuery,
    document_from_file,
    document_from_text,
)
from readio.api.integrations.spotify import SpotifyLivePublishRequest, SpotifyService


def _consume_public_event(event: ReadioEvent) -> None:
    kind: EventKind = event.kind
    stage: EventStage | None = event.stage
    progress_kind: ProgressKind | None = event.progress_kind
    assert kind and stage is not None and progress_kind is not None


def _planned_failure_contract(
    error: PlanNotExecutableError | PlannedOutputError,
) -> tuple[ResolvedPlan, tuple[Diagnostic, ...]]:
    plan: ResolvedPlan = error.plan
    diagnostics: tuple[Diagnostic, ...] = error.diagnostics
    return plan, diagnostics


def public_api_consumer(
    app: Readio,
    sink: AudioSink,
    lines: Iterable[str],
    source: Path,
) -> tuple[ResolvedPlan, RenderResult, ProjectBuildResult, ProjectStatus, DoctorReport]:
    document = document_from_file(source)
    request = PlanRequest(
        operation="render",
        input=InputRequest(document=document, requested_format="auto"),
        synthesis=SynthesisRequest(engine_options={"test-option": "value"}),
        output=OutputRequest(requested_format="wav"),
    )
    event_handler: EventHandler = _consume_public_event
    plan: ResolvedPlan = app.speech.plan(request)
    rendered: RenderResult = app.speech.render_to_sink(request, sink, on_event=event_handler)
    app.speech.render_live(lines, sink)

    file_rendered: RenderResult = app.speech.render_live_to_file(
        lines,
        OutputRequest(requested_format="wav"),
        synthesis=SynthesisRequest(),
    )
    assert file_rendered
    project: ProjectRef = app.projects.create(source)
    mutation: ProjectRoleMutationResult = app.roles.unbind_project_result(project, "narrator")
    app.roles.unbind_project(project, "narrator")
    assert mutation.status
    role_payload: dict[str, JsonValue] = mutation.to_dict()
    assert role_payload["status"] == mutation.status
    status: ProjectStatus = app.projects.status(project)
    built: ProjectBuildResult = app.projects.build(
        project,
        ProjectBuildRequest(target="export"),
    )
    listing: CatalogListing[VoiceInfo] = app.catalog.voices_listing(
        VoiceQuery(language="en-us"),
        discovery=DiscoveryOptions(offline=True),
    )
    voice_listing: CatalogListing[VoiceInfo] = app.catalog.voice_listing(
        "af_sarah", query=VoiceQuery(language="en-us"), discovery=DiscoveryOptions(offline=True)
    )
    lexicon_listing: CatalogListing[LexiconInfo] = app.catalog.lexicon_listing(
        "crane", query=LexiconQuery(language="en-us"), discovery=DiscoveryOptions(offline=True)
    )
    profile: LanguageProfileResolution = app.configuration.resolve_language_profile("en-us")
    patch = LanguageProfilePatch(model=UNSET, lexicons=None, allow_experimental=False)
    updated_profile: LanguageSettings = app.configuration.update_language_profile(
        "en-us", patch, validate_runtime=False
    )
    checked: SSMDCheckResult = app.ssmd.check(document, synthesis=request.synthesis)
    canonical_engine: str = app.catalog.normalize_engine("pipersynth")
    initialized: ConfigurationInitResult = app.configuration.initialize(seed_templates=False)
    report: DoctorReport = app.diagnostics.run()
    integration = SpotifyService(app)
    live_publish = integration.publish_live(
        SpotifyLivePublishRequest(
            lines=lines,
            output=OutputRequest(requested_format="wav"),
            synthesis=SynthesisRequest(),
            title="Consumer typing",
        )
    )

    assert document_from_text("consumer typing")
    assert listing.items
    assert voice_listing.items
    assert lexicon_listing.items
    assert profile.normalized
    assert updated_profile.allow_experimental is False
    assert checked.analysis.provider
    assert canonical_engine == "piper"
    assert initialized.path
    assert integration
    assert live_publish.audio_format == "wav"
    return plan, rendered, built, status, report
