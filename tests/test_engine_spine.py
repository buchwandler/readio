from __future__ import annotations

from typing import ClassVar

import pykokoro
import pytest
from pykokoro import short_sentence_handler

from readio.engines.base import EngineSelection
from readio.engines.pykokoro import PyKokoroEngineAdapter


class _FakeShortSentenceConfig:
    def __init__(self, *, enabled: bool = True, resolve_mode: str | None = None) -> None:
        self.enabled = enabled
        self.resolve_mode = resolve_mode


class _FakePipelineConfig:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


class _FakeGenerationConfig:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


class _FakePipeline:
    instances: ClassVar[list[_FakePipeline]] = []

    def __init__(self, config) -> None:
        self.config = config
        self.__class__.instances.append(self)


@pytest.fixture
def fake_pykokoro(monkeypatch: pytest.MonkeyPatch):
    _FakePipeline.instances = []
    monkeypatch.setattr(pykokoro, "PipelineConfig", _FakePipelineConfig)
    monkeypatch.setattr(pykokoro, "GenerationConfig", _FakeGenerationConfig)
    monkeypatch.setattr(pykokoro, "KokoroPipeline", _FakePipeline)
    monkeypatch.setattr(short_sentence_handler, "ShortSentenceConfig", _FakeShortSentenceConfig)
    return _FakePipeline


@pytest.mark.parametrize(
    ("policy", "expected_enabled", "expected_mode"),
    [
        ("auto", None, None),
        ("off", False, None),
        ("wrap", True, "wrap"),
        ("phrase", True, "phrase"),
        ("randomized-phrase", True, "randomized-phrase"),
    ],
)
def test_active_pykokoro_engine_forwards_short_sentence_policy(
    fake_pykokoro,
    policy: str,
    expected_enabled: bool | None,
    expected_mode: str | None,
) -> None:
    selection = EngineSelection(
        engine="pykokoro",
        target_id="default",
        language="en-us",
        voice="af_heart",
        options={"short_sentence": policy},
    )

    with PyKokoroEngineAdapter().open(selection):
        pass

    config = fake_pykokoro.instances[0].config
    short_sentence = getattr(config, "short_sentence_config", None)
    if expected_enabled is None:
        assert short_sentence is None
    else:
        assert short_sentence is not None
        assert short_sentence.enabled is expected_enabled
        assert short_sentence.resolve_mode == expected_mode
