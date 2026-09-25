from __future__ import annotations

import sys
from types import ModuleType
from typing import ClassVar

import pytest

from readio.engines.base import EngineSelection
from readio.engines.pykokoro import PyKokoroEngineAdapter


class _NativeConfig:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeSynthesizer:
    instances: ClassVar[list[_FakeSynthesizer]] = []

    def __init__(self, config):
        self.config = config
        self.__class__.instances.append(self)

    def close(self):
        pass


@pytest.fixture
def fake_pykokoro(monkeypatch: pytest.MonkeyPatch):
    _FakeSynthesizer.instances = []
    module = ModuleType("pykokoro")
    module.GenerationConfig = _NativeConfig
    module.TokenizerConfig = _NativeConfig
    module.ShortSentenceConfig = _NativeConfig
    module.SynthesisConfig = _NativeConfig
    module.VoiceLevelConfig = _NativeConfig
    module.KokoroSynthesizer = _FakeSynthesizer
    monkeypatch.setitem(sys.modules, "pykokoro", module)
    return _FakeSynthesizer


@pytest.mark.parametrize(
    ("policy", "expected_enabled", "expected_mode"),
    [
        (None, None, None),
        ("off", False, None),
        ("wrap", True, "wrap"),
        ("phrase", True, "phrase"),
        ("randomized-phrase", True, "randomized-phrase"),
    ],
)
def test_pykokoro_adapter_maps_short_sentence_options(
    fake_pykokoro,
    policy: str | None,
    expected_enabled: bool | None,
    expected_mode: str | None,
) -> None:
    options = {"short_sentence": policy} if policy is not None else {}
    selection = EngineSelection(
        engine="pykokoro",
        target_id="v1.0",
        language="en-us",
        voice="af_heart",
        options=options,
    )

    with PyKokoroEngineAdapter().open(selection):
        pass

    config = fake_pykokoro.instances[0].config
    short_sentence = config.short_sentence_config
    if expected_enabled is None:
        assert short_sentence is None
    else:
        assert short_sentence is not None
        assert short_sentence.enabled is expected_enabled
        assert getattr(short_sentence, "resolve_mode", None) == expected_mode
