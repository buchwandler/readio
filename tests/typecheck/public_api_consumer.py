from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from readio.api import (
    AudioSink,
    CatalogListing,
    ConfigurationInitResult,
    DiscoveryOptions,
    DoctorReport,
    InputRequest,
    LanguageProfileResolution,
    LexiconInfo,
    LexiconQuery,
    OutputRequest,
    PlanRequest,
    ProjectBuildRequest,
    ProjectBuildResult,
    ProjectRef,
    ProjectStatus,
    Readio,
    RenderResult,
    ResolvedPlan,
    SynthesisRequest,
    VoiceInfo,
    VoiceQuery,
    document_from_file,
    document_from_text,
)
from readio.api.integrations.spotify import SpotifyLivePublishRequest, SpotifyService


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
    plan: ResolvedPlan = app.speech.plan(request)
    rendered: RenderResult = app.speech.render_to_sink(request, sink)
    app.speech.render_live(lines, sink)

    file_rendered: RenderResult = app.speech.render_live_to_file(
        lines,
        OutputRequest(requested_format="wav"),
        synthesis=SynthesisRequest(),
    )
    assert file_rendered
    project: ProjectRef = app.projects.create(source)
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
    assert initialized.path
    assert integration
    assert live_publish.audio_format == "wav"
    return plan, rendered, built, status, report
