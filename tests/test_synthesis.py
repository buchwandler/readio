from __future__ import annotations

from argparse import Namespace

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
        "pause_mode": None,
        "unit": None,
    }
    defaults.update(values)
    return Namespace(**defaults)


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

    resolved = resolve_synthesis(cfg, _args(lang="de", voice="custom", lexicons=["crane"]))
    assert resolved.model == "de-thorsten"
    assert resolved.voice == "custom"
    assert resolved.lexicons == ("crane",)


def test_resolution_no_lexicons_clears_inherited_values_and_locale_falls_back() -> None:
    cfg = ReadioConfig(
        reader=ReaderSettings(lang="en-us"),
        languages={"de": LanguageSettings(model="de-thorsten", lexicons=("crane",))},
    )
    resolved = resolve_synthesis(cfg, _args(lang="de-at", no_lexicons=True))
    assert resolved.language == "de-at"
    assert resolved.model == "de-thorsten"
    assert resolved.lexicons is None


def test_resolution_without_model_preserves_automatic_selection() -> None:
    cfg = ReadioConfig(reader=ReaderSettings(voice="af_sarah", lang="en-us"))
    resolved = resolve_synthesis(cfg)
    assert resolved.model is None
    assert resolved.voice == "af_sarah"
