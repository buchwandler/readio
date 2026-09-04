from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

from readio.config import LanguageSettings, ReaderSettings, ReadioConfig
from readio.models import ModelInfo
from readio.synthesis import ResolvedModel, resolve_synthesis

MODEL = ModelInfo(
    id="de-thorsten",
    source="huggingface",
    languages=("de",),
    voices=("thorsten",),
    default_voice="thorsten",
    qualities=("int8", "fp32"),
    g2p_backend="kokorog2p",
    lexicons=("gold", "crane"),
    frontend="kokorog2p-de-thorsten-v1",
    status="ready",
    experimental=False,
    runtime_available=True,
    redistribution_allowed=True,
    distribution_id="hf-de-thorsten",
    provider="huggingface",
    sample_rate=24000,
    max_tokens=510,
)


def _args(**updates: object) -> Namespace:
    values = {
        "lang": None,
        "model": None,
        "model_source": None,
        "quality": None,
        "voice": None,
        "lexicons": None,
        "no_lexicons": False,
        "allow_experimental": False,
        "speed": None,
        "pause_mode": None,
        "unit": None,
        "offline": False,
        "refresh": False,
    }
    values.update(updates)
    return Namespace(**values)


def test_model_source_selects_discovery_preference(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def discover(model_id: str, **kwargs: object):
        calls.append(kwargs)
        return MODEL, SimpleNamespace(
            registry_source="fixture", cache_fallback=False, offline=True, refreshed=False
        )

    monkeypatch.setattr("readio.synthesis.get_model_info", discover)
    cfg = ReadioConfig(reader=ReaderSettings(lang="de"))
    resolved = resolve_synthesis(
        cfg,
        _args(model="de-thorsten", model_source="huggingface", offline=True),
    )

    assert calls == [{"offline": True, "refresh": False, "preference": "huggingface"}]
    assert resolved.source == "huggingface"
    assert resolved.resolved_model == ResolvedModel.from_info(MODEL)
    assert resolved.discovery_offline is True


def test_persisted_model_is_discovered_and_language_override_does_not_leak_global_voice(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "readio.synthesis.get_model_info",
        lambda *args, **kwargs: (MODEL, SimpleNamespace(registry_source="fixture")),
    )
    cfg = ReadioConfig(
        reader=ReaderSettings(voice="af_sarah", lang="en-us"),
        languages={"de": LanguageSettings(model="de-thorsten")},
    )

    resolved = resolve_synthesis(cfg, _args(lang="de"))

    assert resolved.model == "de-thorsten"
    assert resolved.voice == "thorsten"
    assert resolved.quality == "fp32"


def test_language_override_without_model_leaves_voice_for_pykokoro() -> None:
    cfg = ReadioConfig(reader=ReaderSettings(voice="af_sarah", lang="en-us"))
    resolved = resolve_synthesis(cfg, _args(lang="de"))
    assert resolved.language == "de"
    assert resolved.voice is None
    assert resolved.model is None
