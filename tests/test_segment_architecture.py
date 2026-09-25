from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

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


def _identity_fixture(*, morph="Number=Sing", tag="NNP", phonemes="ˈæl.fə"):
    token = SimpleNamespace(
        spoken_start=0,
        spoken_end=5,
        text="alpha",
        language="en",
        lemma="alpha",
        pos="PROPN",
        tag=tag,
        morph=morph,
    )
    annotation = SimpleNamespace(
        id="pron-1",
        kind="pronunciation",
        spoken_start=0,
        spoken_end=5,
        attrs={"ph": phonemes, "alphabet": "ipa", "language": "en"},
    )
    segment = SimpleNamespace(
        id="segment-1",
        text="alpha",
        language="en",
        spoken_start=0,
        spoken_end=5,
        token_indices=(0,),
        annotation_ids=("pron-1",),
        directives=SimpleNamespace(voice=None, pronunciation=None),
    )
    plan = SimpleNamespace(tokens=(token,), annotations=(annotation,))
    return plan, segment


def _identity_profile(*, speed=1.0, voice_level="off", revision="rev-1", cache_dir="/tmp/a"):
    return {
        "canonical": {
            "engine": "pykokoro",
            "engine_version": "0.10.0",
            "target_id": "en-us-test",
            "metadata": {"source_revision": revision},
            "options": {
                "speed": speed,
                "voice_level": voice_level,
                "cache_dir": cache_dir,
            },
        }
    }


def test_speech_identity_hashes_exact_token_and_pronunciation_context() -> None:
    plan, segment = _identity_fixture()
    profile = _identity_profile()
    baseline = segment_speech_hash(plan, segment, profile)

    for changed_plan, changed_segment in (
        (_identity_fixture(morph="Number=Plur")[0], segment),
        (_identity_fixture(tag="NNPS")[0], segment),
        (_identity_fixture(phonemes="ˈɑl.fə")[0], segment),
    ):
        assert segment_speech_hash(changed_plan, changed_segment, profile) != baseline

    changed_payload = segment_speech_payload(plan, segment, profile)
    assert changed_payload["tokens"][0]["morph"] == "Number=Sing"
    assert changed_payload["pronunciation_spans"][0]["phonemes"] == "ˈæl.fə"


def test_speech_identity_includes_speed_voice_level_and_target_revision_but_not_cache_path() -> (
    None
):
    plan, segment = _identity_fixture()
    baseline = segment_speech_hash(plan, segment, _identity_profile())

    assert segment_speech_hash(plan, segment, _identity_profile(cache_dir="/tmp/b")) == baseline
    operational = _identity_profile(cache_dir="/tmp/b")
    operational["canonical"]["options"].update(verbosity=3, progress_callback="callback")
    assert segment_speech_hash(plan, segment, operational) == baseline
    assert segment_speech_hash(plan, segment, _identity_profile(speed=1.2)) != baseline
    assert (
        segment_speech_hash(plan, segment, _identity_profile(voice_level="calibrated")) != baseline
    )
    assert segment_speech_hash(plan, segment, _identity_profile(revision="rev-2")) != baseline


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
