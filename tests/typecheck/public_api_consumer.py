from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from readio.api import (
    AudioSink,
    CatalogListing,
    DiscoveryOptions,
    DoctorReport,
    InputRequest,
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
from readio.api.integrations.spotify import SpotifyService


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
    report: DoctorReport = app.diagnostics.run()
    integration = SpotifyService(app)

    assert document_from_text("consumer typing")
    assert listing.items
    assert integration
    return plan, rendered, built, status, report
