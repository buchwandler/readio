from __future__ import annotations

from contextlib import contextmanager

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
        self.index = index
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
        return EngineSelection(self.id, "fake-target", request.language or "en-us", voice=request.voice, options=dict(request.options)), ()

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
