from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from readio.api import (
    AUDIOBOOK_EXPORT_FORMAT,
    UNSET,
    AudiobookChapter,
    AudiobookExportOptions,
    AudiobookExportResult,
    AudiobookInspection,
    AudiobookProjectResult,
    AudioSink,
    CatalogListing,
    ConfigurationInitResult,
    Diagnostic,
    DiscoveryOptions,
    DoctorReport,
    Document,
    DocumentProvenance,
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
    ProjectRole,
    ProjectRoleInspection,
    ProjectRoleMutationResult,
    ProjectSettings,
    ProjectSettingsPatch,
    ProjectStatus,
    ProjectSynthesisSettings,
    Readio,
    ReadioEvent,
    RenderResult,
    ResolvedPlan,
    RoleBinding,
    SelfTestRequest,
    SelfTestResult,
    SSMDCheckResult,
    SynthesisRequest,
    TextVerificationResult,
    TimestampComparisonRequest,
    TimestampComparisonResult,
    TimestampGenerationRequest,
    TimestampGenerationResult,
    TimestampSelfTestRequest,
    TimestampSelfTestResult,
    TranscriptionResult,
    VerificationOptions,
    VerificationService,
    VoiceInfo,
    VoiceMatrixRequest,
    VoiceMatrixResult,
    VoiceQuery,
    VoiceTarget,
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
    provenance: DocumentProvenance | None = document.provenance
    assert provenance is None or provenance.converter_version is not None
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
    app.projects.resolve_synthesis(project, SynthesisRequest(), use_saved_settings=False)
    target: VoiceTarget = VoiceTarget("piper", "en_US-amy-medium", target_id="amy-asset")
    global_binding: RoleBinding = app.roles.bind_global(
        "typed_guest", target.voice, engine=target.engine
    )
    global_target: VoiceTarget = global_binding.target
    project_role: ProjectRole = app.roles.bind_project(
        project, "narrator", target.voice, engine=target.engine
    )
    inspection: ProjectRoleInspection = app.roles.inspect_project(project)
    effective_target: VoiceTarget | None = project_role.effective_target
    assert global_target.engine == target.engine
    assert inspection.roles
    assert effective_target is not None
    mutation: ProjectRoleMutationResult = app.roles.unbind_project_result(project, "narrator")
    role_json: dict[str, JsonValue] = project_role.to_dict()
    mutation_target: VoiceTarget | None = mutation.previous_project_target
    assert role_json and mutation_target is not None
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
    initialized: ConfigurationInitResult = app.configuration.initialize()
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


def public_audiobook_consumer(
    app: Readio, project: ProjectRef, cover: Path | None = None
) -> AudiobookExportResult:
    options = AudiobookExportOptions(
        title="Typed audiobook",
        cover=cover,
        bitrate="96k",
    )
    result: AudiobookExportResult = app.audiobooks.export(project, options)
    assert result.format == AUDIOBOOK_EXPORT_FORMAT
    return result


def public_book_ingestion_consumer(
    app: Readio,
) -> tuple[
    Document,
    ProjectRef,
    AudiobookInspection,
    AudiobookChapter | None,
    AudiobookProjectResult,
]:
    document: Document = document_from_file(Path("report.pdf"))
    project: ProjectRef = app.projects.create(Path("report.pdf"))
    inspection: AudiobookInspection = app.audiobooks.inspect(Path("book.ssmdbook.zip"))
    typed_chapter: AudiobookChapter | None = inspection.chapters[0] if inspection.chapters else None
    audiobook: AudiobookProjectResult = app.audiobooks.create_project_result(Path("book.ssmdbook"))
    return document, project, inspection, typed_chapter, audiobook


def public_project_settings_consumer(app: Readio, project: ProjectRef) -> ProjectSettings:
    app.projects.configure(
        project, ProjectSettings(synthesis=ProjectSynthesisSettings(engine="piper"))
    )
    return app.projects.update_settings(
        project,
        ProjectSettingsPatch(synthesis=ProjectSynthesisSettings(voice="en_US-amy-medium")),
    )


def public_audiobook_build_consumer(app: Readio, project: ProjectRef) -> AudiobookExportResult:
    return app.audiobooks.build(project)


def public_timestamp_consumer(
    app: Readio, audio: Path, project: ProjectRef
) -> tuple[
    TimestampComparisonResult,
    TimestampGenerationResult,
    TimestampSelfTestResult,
    dict[str, JsonValue],
]:
    comparison: TimestampComparisonResult = app.verification.compare_timestamps(
        TimestampComparisonRequest(audio=audio, text="typed consumer")
    )
    generation: TimestampGenerationResult = app.verification.generate_timestamps(
        TimestampGenerationRequest(project=project)
    )
    selftest: TimestampSelfTestResult = app.verification.selftest_timestamps(
        TimestampSelfTestRequest(case="readio-timestamps-en-v1")
    )
    payload: dict[str, JsonValue] = comparison.to_dict()
    assert comparison.timing_structure["status"]
    assert generation.schema
    assert selftest.schema
    return comparison, generation, selftest, payload


def public_verification_consumer(
    app: Readio, audio: Path, project: ProjectRef, output: Path
) -> tuple[
    VerificationService,
    TranscriptionResult,
    TextVerificationResult,
    SelfTestResult,
    VoiceMatrixResult,
    TimestampGenerationResult,
    dict[str, JsonValue],
]:
    verification: VerificationService = app.verification
    options = VerificationOptions()
    transcription: TranscriptionResult = verification.transcribe(audio, options)
    text_result: TextVerificationResult = verification.verify_text(
        audio, "typed public API", options=options
    )
    synthesis = SynthesisRequest(language="en-us", engine="kokoro", model="v1.0")
    e2e: SelfTestResult = verification.run_e2e(
        SelfTestRequest(synthesis=synthesis, output=output / "e2e")
    )
    voices: VoiceMatrixResult = verification.generate_voices(
        VoiceMatrixRequest(synthesis=synthesis, output=output / "voices")
    )
    timestamps: TimestampGenerationResult = verification.generate_timestamps(
        TimestampGenerationRequest(
            project=project, synthesis=synthesis, output=output / "timestamps"
        )
    )
    reports: dict[str, JsonValue] = {
        "e2e": e2e.to_dict(),
        "voices": voices.to_dict(),
        "timestamps": timestamps.to_dict(),
    }
    assert transcription.text
    assert text_result.status
    assert e2e.overall_status
    assert voices.overall_status
    assert timestamps.overall_status
    return verification, transcription, text_result, e2e, voices, timestamps, reports
