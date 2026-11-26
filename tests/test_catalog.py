from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from readio import api, engines, voices
from readio.api import Readio
from readio.api.types import EngineInfo, EngineOptionInfo, TargetInfo, VoiceInfo
from readio.errors import CatalogError


def test_readio_catalog_methods_normalize_mapping_and_attribute_descriptors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine_records = [
        {
            "id": "kitten",
            "installed": True,
            "installed_version": "1.2.0",
            "runnable": True,
            "capabilities": {"named_voices": True, "speakers": False},
            "missing_dependency": None,
        },
        SimpleNamespace(id="bare", installed=False),
    ]
    target_records = [
        {
            "id": "kitten-v1",
            "languages": ["en-US", "de-DE"],
            "status": "ready",
            "runtime_available": True,
            "default_voice": "voice-a",
            "voices": ["voice-a", "voice-b"],
            "speakers": ["1", "2", "3"],
            "qualities": ["standard", "high"],
            "sample_rate": 24000,
            "voice_details": [{"language": "en-US", "language_label": "English"}],
            "capabilities": {"named_voices": True, "speakers": True},
            "metadata": {"voice_mode": "named"},
        },
        SimpleNamespace(
            id="reference-only",
            capabilities=frozenset({"reference_voice"}),
            runtime_available=False,
        ),
    ]
    voice_records = [
        {
            "voice": "voice-a",
            "languages": ["en-US"],
            "gender": "female",
            "locale": "en-US",
            "kind": "builtin",
            "installed": True,
            "metadata": {
                "target": {"default_voice": "voice-a"},
                "voice": {"language_label": "English"},
            },
        },
        SimpleNamespace(
            selector="kitten:kitten-v1#7",
            speaker=7,
            kind="speaker",
            languages=("en-US",),
            installed=False,
        ),
        {"id": "voice-b"},
    ]
    option_records = [
        {
            "name": "temperature",
            "value_type": "float",
            "required": True,
            "default": 0.5,
            "choices": [0.1, 0.5],
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "Synthesis temperature",
            "identity_affecting": True,
            "runtime_only": False,
        },
        SimpleNamespace(name="threads", minimum=True, maximum=8),
    ]
    monkeypatch.setattr(engines.voicerender, "list_engines", lambda: engine_records)
    monkeypatch.setattr(
        engines.voicerender,
        "discover_targets",
        lambda *_args, **_kwargs: target_records,
    )
    monkeypatch.setattr(voices.voicerender, "list_voices", lambda **_kwargs: voice_records)
    monkeypatch.setattr(
        engines.voicerender,
        "engine_options",
        lambda *_args, **_kwargs: option_records,
    )

    service = Readio()
    listed_engines = service.list_engines()
    assert listed_engines == (
        EngineInfo(
            id="kitten",
            version="1.2.0",
            installed=True,
            runnable=True,
            features=("voices",),
        ),
        EngineInfo(
            id="bare",
            version=None,
            installed=False,
            runnable=False,
            features=(),
        ),
    )
    targets = service.list_targets("kitten", language="en-US", offline=True, refresh=True)
    assert targets == (
        TargetInfo(
            id="kitten-v1",
            languages=("en-US", "de-DE"),
            status="ready",
            runtime_available=True,
            default_voice="voice-a",
            voice_count=2,
            qualities=("standard", "high"),
            voice_mode="named",
            sample_rate=24000,
            features=("voices", "speakers"),
            reference_only=False,
        ),
        TargetInfo(
            id="reference-only",
            languages=(),
            status="ready",
            runtime_available=False,
            default_voice=None,
            voice_count=0,
            qualities=(),
            voice_mode=None,
            sample_rate=None,
            features=("reference audio",),
            reference_only=True,
        ),
    )
    listed_voices = service.list_voices(engine="kitten", target="kitten-v1")
    assert listed_voices == (
        VoiceInfo(
            id="voice-a",
            language="en-US",
            locale="en-US",
            language_label="English",
            gender="female",
            kind="builtin",
            default=True,
            runtime_available=True,
        ),
        VoiceInfo(
            id="7",
            language="en-US",
            locale=None,
            language_label=None,
            gender="unknown",
            kind="speaker",
            default=False,
            runtime_available=False,
        ),
        VoiceInfo(
            id="voice-b",
            language=None,
            locale=None,
            language_label=None,
            gender="unknown",
            kind=None,
            default=False,
            runtime_available=True,
        ),
    )
    options = service.list_engine_options("kitten", target="kitten-v1")
    assert options == (
        EngineOptionInfo(
            name="temperature",
            value_type="float",
            required=True,
            default=0.5,
            choices=(0.1, 0.5),
            minimum=0.0,
            maximum=1.0,
            description="Synthesis temperature",
            identity_affecting=True,
            runtime_only=False,
        ),
        EngineOptionInfo(
            name="threads",
            value_type=None,
            required=False,
            default=None,
            choices=(),
            minimum=None,
            maximum=8,
            description="",
            identity_affecting=False,
            runtime_only=False,
        ),
    )
    assert all(isinstance(item, EngineInfo) for item in listed_engines)
    assert all(isinstance(item, TargetInfo) for item in targets)
    assert all(isinstance(item, VoiceInfo) for item in listed_voices)
    assert all(isinstance(item, EngineOptionInfo) for item in options)


def test_readio_catalog_wraps_specialist_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    service = Readio()
    failure = RuntimeError("catalog backend failed")

    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise failure

    monkeypatch.setattr(engines.voicerender, "list_engines", fail)
    with pytest.raises(CatalogError) as caught:
        service.list_engines()
    assert caught.value.operation == "list_engines"
    assert caught.value.cause is failure

    monkeypatch.setattr(engines.voicerender, "discover_targets", fail)
    with pytest.raises(CatalogError) as caught:
        service.list_targets("engine-a")
    assert caught.value.operation == "list_targets"
    assert caught.value.engine == "engine-a"
    assert caught.value.cause is failure

    monkeypatch.setattr(voices.voicerender, "list_voices", fail)
    with pytest.raises(CatalogError) as caught:
        service.list_voices(engine="engine-a", target="target-a")
    assert caught.value.operation == "list_voices"
    assert caught.value.engine == "engine-a"
    assert caught.value.target == "target-a"
    assert caught.value.cause is failure

    monkeypatch.setattr(engines.voicerender, "engine_options", fail)
    with pytest.raises(CatalogError) as caught:
        service.list_engine_options("engine-a", target="target-a")
    assert caught.value.operation == "list_engine_options"
    assert caught.value.engine == "engine-a"
    assert caught.value.target == "target-a"
    assert caught.value.cause is failure


def test_raw_module_catalog_delegates_keep_specialist_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_engines = [{"id": "engine-a", "specialist": object()}]
    raw_targets = (SimpleNamespace(id="target-a"),)
    raw_voices = (SimpleNamespace(id="voice-a"),)
    raw_options = (SimpleNamespace(name="option-a"),)
    monkeypatch.setattr(engines.voicerender, "list_engines", lambda: raw_engines)
    monkeypatch.setattr(
        engines.voicerender,
        "discover_targets",
        lambda *_args, **_kwargs: raw_targets,
    )
    monkeypatch.setattr(voices.voicerender, "list_voices", lambda **_kwargs: raw_voices)
    monkeypatch.setattr(
        engines.voicerender,
        "engine_options",
        lambda *_args, **_kwargs: raw_options,
    )

    assert api.list_engines() is raw_engines
    assert api.discover_engine_targets("engine-a") is raw_targets
    assert api.list_voices(engine="engine-a", target="target-a") is raw_voices
    assert api.list_engine_options("engine-a", target="target-a") is raw_options


def test_target_language_codes_round_trip_from_targets_to_voices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_target = {
        "id": "kitten-v1",
        "languages": ["en-US"],
        "voices": ["voice-a"],
        "voice_details": [{"language": "en-US", "language_label": "English"}],
    }
    voice_calls: list[dict[str, Any]] = []

    def list_voices(**kwargs: Any) -> list[dict[str, str]]:
        voice_calls.append(kwargs)
        return [{"id": "voice-a", "language": "en-US", "language_label": "English"}]

    monkeypatch.setattr(
        engines.voicerender,
        "discover_targets",
        lambda *_args, **_kwargs: [raw_target],
    )
    monkeypatch.setattr(voices.voicerender, "list_voices", list_voices)

    service = Readio()
    target = service.list_targets("kitten")[0]
    service.list_voices(
        engine="kitten",
        target=target.id,
        language=target.languages[0],
    )

    assert target.languages == ("en-US",)
    assert voice_calls[0]["language"] == "en-US"
