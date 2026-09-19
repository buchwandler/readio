from contextlib import contextmanager

import numpy as np

from readio.engines.base import EngineCapabilities, EngineSelection
from readio.plan import InputRequest, OutputRequest, PlanRequest, SynthesisRequest


class Result:
    def __init__(self, index):
        self.index = index
        self.audio = np.full(160, 0.1, dtype=np.float32)
        self.sample_rate = 24000
        self.markers = []

    def release_audio(self):
        pass


class Prepared:
    def __init__(self, plan):
        self.plan = plan

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def render(self, indices=None):
        for index in indices or range(len(self.plan.units)):
            yield Result(index)


class Session:
    def prepare_plan(self, plan, *, options):
        return Prepared(plan)


class Adapter:
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
            yield Session()

        return session()


def request(project):
    return PlanRequest(
        "render",
        InputRequest(project.document()),
        SynthesisRequest(engine="fake", voice="fake-voice"),
        OutputRequest(mode="file", requested_format="wav", force=True),
    )
