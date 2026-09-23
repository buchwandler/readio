from __future__ import annotations

from dataclasses import replace

from project_support import Adapter, request
from utterplan import ProsodyDirective, ResolvedPause

from readio.config import ReaderSettings, ReadioConfig
from readio.document import document_from_text
from readio.engines.registry import _registry
from readio.planning.compiler import compile_semantic_plan
from readio.planning.policy import PlanningPolicy
from readio.project import init_project, read_json
from readio.stages.composition import compose_project, seconds_to_frames
from readio.stages.planning import plan_project
from readio.stages.speech_identity import segment_speech_hash, segment_speech_payload
from readio.stages.synthesis import synthesize_project


def test_canonical_speech_identity_excludes_composition_directives() -> None:
    plan = compile_semantic_plan(
        document_from_text("Alpha."),
        planning=PlanningPolicy.from_semantic_config(ReadioConfig()),
    ).plan
    segment = plan.segments[0]
    directives = replace(
        segment.directives,
        prosody=ProsodyDirective(rate="slow", pitch="high", volume="loud"),
    )
    changed = replace(
        segment,
        pause_after=ResolvedPause(seconds=2.0, events=("pause",)),
        directives=directives,
    )
    profile = {"engine": "fake", "voice": "test"}

    assert segment_speech_hash(plan, segment, profile) == segment_speech_hash(
        plan, changed, profile
    )
    assert "pause_after" not in segment_speech_payload(plan, changed, profile)
    assert "prosody" not in segment_speech_payload(plan, changed, profile)["synthesis_directives"]


def test_composition_uses_segment_layout_without_opening_tts(tmp_path, monkeypatch) -> None:
    adapter = Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "book.txt"
    source.write_text("Alpha.\n\nBeta.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    plan_project(project, cfg)
    synthesize_project(project, cfg, request=request(project))
    project.paths["synthesis_trace"].unlink()
    monkeypatch.setattr(
        "readio.engines.registry.get_engine",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("TTS touched")),
    )

    compose_project(project)
    timeline = read_json(project.paths["composition_timeline"])
    silences = [item for item in timeline["layout"] if item["kind"] == "silence"]

    assert silences[0]["frames"] == seconds_to_frames(1.0, 24000)
    assert [item["kind"] for item in timeline["layout"]] == ["speech", "silence", "speech"]
