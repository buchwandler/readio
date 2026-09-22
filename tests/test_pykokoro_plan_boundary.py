from __future__ import annotations

from readio.engines.pykokoro import PyKokoroEngineSession


class _Pipeline:
    def __init__(self) -> None:
        self.prepare_overrides: dict[str, object] | None = None
        self.audio_job_overrides: dict[str, object] | None = None
        self.segment_overrides: dict[str, object] | None = None

    def prepare_plan_units(self, plan, **overrides):
        self.prepare_overrides = dict(overrides)
        return object()


    def prepare_plan_segments(self, plan, **overrides):
        self.segment_overrides = dict(overrides)
        return object()
    def to_audio_job_from_plan(self, plan, **overrides):
        self.audio_job_overrides = dict(overrides)
        return object()


def _options() -> dict[str, object]:
    return {
        "pause_mode": "auto",
        "pause_sentence": 0.35,
        "unit": "sentence",
        "speed": 1.15,
        "lexicons": ("gold",),
        "model_source": "auto",
        "spacy": "lg",
    }


def test_prepare_plan_does_not_forward_plan_owned_options() -> None:
    pipeline = _Pipeline()
    session = PyKokoroEngineSession(pipeline)

    session.prepare_plan(object(), options=_options())

    assert pipeline.prepare_overrides == {
        "speed": 1.15,
        "lexicons": ("gold",),
        "model_source": "auto",
    }



def test_prepare_segments_does_not_forward_model_speed_or_generation() -> None:
    pipeline = _Pipeline()
    session = PyKokoroEngineSession(pipeline)
    session.prepare_segments(object(), options=_options())

    assert pipeline.segment_overrides == {
        "lexicons": ("gold",),
        "model_source": "auto",
    }

def test_to_audio_job_does_not_forward_plan_owned_options() -> None:
    pipeline = _Pipeline()
    session = PyKokoroEngineSession(pipeline)

    session.to_audio_job(object(), options=_options())

    assert pipeline.audio_job_overrides == {
        "speed": 1.15,
        "lexicons": ("gold",),
        "model_source": "auto",
    }
