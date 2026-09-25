from __future__ import annotations

from argparse import Namespace

import pytest

from readio.config import LanguageSettings, ReaderSettings, ReadioConfig
from readio.synthesis import resolve_synthesis


def _args(**values: object) -> Namespace:
    defaults = {
        "lang": None,
        "model": None,
        "model_source": None,
        "quality": None,
        "voice": None,
        "lexicons": None,
        "no_lexicons": False,
        "allow_experimental": False,
        "speed": None,
        "voice_level": None,
        "engine": None,
        "pause_mode": None,
        "unit": None,
    }
    defaults.update(values)
    return Namespace(**defaults)


def test_resolve_synthesis_uses_auto_pause_default() -> None:
    resolved = resolve_synthesis(ReadioConfig(), _args())
    assert resolved.pause_mode == "auto"


def test_resolution_applies_language_profile_and_cli_precedence() -> None:
    cfg = ReadioConfig(
        reader=ReaderSettings(voice="af_sarah", lang="en-us"),
        languages={
            "de": LanguageSettings(
                model="de-thorsten",
                source="github",
                quality="fp32",
                voice="thorsten",
                lexicons=("gold",),
            )
        },
    )

    resolved = resolve_synthesis(cfg, _args(lang="de", voice="thorsten", lexicons=["crane"]))
    assert resolved.model == "de-thorsten"
    assert resolved.voice == "thorsten"
    assert resolved.lexicons == ("crane",)


def test_resolution_no_lexicons_clears_inherited_values_and_locale_falls_back() -> None:
    cfg = ReadioConfig(
        reader=ReaderSettings(lang="en-us"),
        languages={"de": LanguageSettings(model="de-thorsten", lexicons=("crane",))},
    )
    resolved = resolve_synthesis(cfg, _args(lang="de-at", no_lexicons=True))
    assert resolved.language == "de-at"
    assert resolved.model == "de-thorsten"
    assert resolved.lexicons == ()


def test_resolution_without_model_preserves_automatic_selection() -> None:
    cfg = ReadioConfig(reader=ReaderSettings(voice="af_sarah", lang="en-us"))
    resolved = resolve_synthesis(cfg)
    assert resolved.model is None
    assert resolved.voice == "af_sarah"


def test_resolve_synthesis_threads_engine_speed_and_voice_level() -> None:
    resolved = resolve_synthesis(
        ReadioConfig(),
        _args(speed=1.25, voice_level="calibrated"),
    )
    assert resolved.speed == 1.25
    assert resolved.voice_level == "calibrated"


def test_pocket_rejects_non_default_synthesis_speed() -> None:
    with pytest.raises(ValueError, match="synthesis.speed_unsupported"):
        resolve_synthesis(ReadioConfig(), _args(engine="pocket", speed=1.25))
