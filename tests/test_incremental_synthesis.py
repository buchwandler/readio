from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np

from readio.config import ReaderSettings, ReadioConfig
from readio.engines.base import EngineCapabilities, EngineSelection
from readio.engines.registry import _registry
from readio.plan import InputRequest, OutputRequest, PlanRequest, SynthesisRequest
from readio.project import init_project
from readio.stages.planning import plan_project
from readio.stages.synthesis import synthesize_project


class _Result:
    def __init__(self, index: int):
        self.descriptor = SimpleNamespace(index=index)
        self.audio = np.full(160, 0.1, dtype=np.float32)
        self.sample_rate = 24000
        self.markers = []

    def release_audio(self):
        pass


class _Prepared:
    def __init__(self, plan):
        self.plan = plan

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def render(self, indices=None):
        for index in indices or range(len(self.plan.units)):
            yield _Result(index)


class _Session:
    def prepare_plan(self, plan, *, options):
        return _Prepared(plan)


class _Adapter:
    id = "fake"

    def __init__(self):
        self.open_calls = 0

    def version(self):
        return "fake-1"

    def capabilities(self):
        return EngineCapabilities(id=self.id)

    def resolve(self, request):
        return EngineSelection(
            self.id,
            "fake-target",
            request.language or "en-us",
            voice=request.voice,
            options=dict(request.options),
        ), ()

    def planner_config(self, selection, planning):
        return None

    def open(self, selection):
        self.open_calls += 1

        @contextmanager
        def session():
            yield _Session()

        return session()


def _request(project):
    return PlanRequest(
        "render",
        InputRequest(project.document()),
        SynthesisRequest(engine="fake", voice="fake-voice"),
        OutputRequest(mode="file", requested_format="wav", force=True),
    )


def test_complete_cache_hit_does_not_open_engine_and_edit_rebuilds_one_unit(tmp_path, monkeypatch):
    adapter = _Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "book.txt"
    source.write_text("Alpha.\n\nBeta.\n\nGamma.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    plan_project(project, cfg)
    first = synthesize_project(project, cfg, request=_request(project))
    assert first["rendered"] == len(first["selection"].unit_indices)
    assert adapter.open_calls == 1
    second = synthesize_project(project, cfg, request=_request(project))
    assert second["rendered"] == 0
    assert second["reused"] == first["rendered"]
    assert adapter.open_calls == 1
    project_source = project.root / "source" / "book.txt"
    project_source.write_text("Alpha.\n\nChanged.\n\nGamma.", encoding="utf-8")
    plan_project(project, cfg)
    third = synthesize_project(project, cfg, request=_request(project))
    assert third["rendered"] == 1
    assert third["reused"] == 2
    assert adapter.open_calls == 2


def test_project_synthesis_emits_lifecycle_events_and_skips_engine_on_cache_hit(tmp_path, monkeypatch):
    adapter = _Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "book.txt"
    source.write_text("Alpha.\n\nBeta.", encoding="utf-8")
    project = init_project(source, tmp_path / "book.readio")
    plan_project(project, cfg)
    events = []
    synthesize_project(project, cfg, request=_request(project), on_event=events.append)
    assert [event.kind for event in events] == [
        "profile_resolved",
        "cache_scanned",
        "engine_open_started",
        "engine_open_finished",
        "prepare_started",
        "prepare_finished",
        "unit_started",
        "unit_finished",
        "unit_started",
        "unit_finished",
        "activation_started",
        "activation_finished",
        "complete",
    ]
    assert events[6].unit_id == "unit-0000"
    assert events[7].completed == 1
    assert events[7].text == "Alpha."
    assert events[7].details["segment_ids"] == ["seg-000000"]
    assert events[-1].details["rendered"] == 2

    events.clear()
    second = synthesize_project(project, cfg, request=_request(project), on_event=events.append)
    assert second["rendered"] == 0
    assert adapter.open_calls == 1
    assert [event.kind for event in events] == [
        "profile_resolved",
        "cache_scanned",
        "activation_started",
        "activation_finished",
        "complete",
    ]


def test_project_synthesis_merges_document_voice_bindings_with_explicit_overrides(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from readio.stages import synthesis as synthesis_stage

    adapter = _Adapter()
    monkeypatch.setitem(_registry._adapters, "fake", adapter)
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice"))
    source = tmp_path / "episode.ssmd"
    source.write_text(
        "---\nvoice_bindings:\n  kokoro:\n    narrator: af_heart\n    guest: af_bella\n---\n"
        '<div voice="narrator">Hello.</div>',
        encoding="utf-8",
    )
    project = init_project(source, tmp_path / "episode.readio")
    plan_project(project, cfg)
    request = replace(_request(project), voice_bindings={"narrator": "af_sarah"})
    captured = {}

    def resolve(_cfg, resolved_request):
        captured["request"] = resolved_request
        return SimpleNamespace(
            plan=SimpleNamespace(ok=True, diagnostics=()),
            selection=EngineSelection(
                engine="fake",
                target_id="fake-target",
                language="en-us",
                voice="fake-voice",
            ),
        )

    monkeypatch.setattr(synthesis_stage, "resolve_execution_v2", resolve)
    synthesis_stage._resolve_profile(project, cfg, request)

    resolved_request = captured["request"]
    assert resolved_request.input.document.format == "ssmd"
    assert dict(resolved_request.voice_bindings) == {
        "narrator": "af_sarah",
        "guest": "af_bella",
    }
