from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import pytest

from readio import cli
from readio.config import (
    LanguageSettings,
    ReaderSettings,
    ReadioConfig,
    dumps_config,
    load_config,
)
from readio.models import ModelDiscoveryError, ModelInfo, validate_language_settings
from readio.ssmd import language_detection_hint
from readio.synthesis import resolve_synthesis

MODEL = ModelInfo(
    id="de-thorsten",
    source="github",
    languages=("de",),
    voices=("thorsten",),
    default_voice="thorsten",
    qualities=("fp32",),
    g2p_backend="kokorog2p",
    lexicons=("gold", "crane"),
    frontend="kokorog2p-de-thorsten-v1",
    status="ready",
    experimental=False,
    runtime_available=True,
    redistribution_allowed=True,
)
DISCOVERY = SimpleNamespace(
    registry_source="fixture", cache_fallback=False, offline=True, refreshed=False
)


def _args(**values: object) -> Namespace:
    defaults = {
        "lang": "de",
        "model": "de-thorsten",
        "model_source": "github",
        "quality": "fp32",
        "voice": "thorsten",
        "lexicons": None,
        "no_lexicons": False,
        "auto_lexicons": False,
        "g2p_fallback": None,
        "lexicon_data_policy": None,
        "language_detection": None,
        "detect_languages": None,
        "allow_experimental": False,
        "speed": None,
        "pause_mode": None,
        "unit": None,
        "offline": True,
        "refresh": False,
    }
    defaults.update(values)
    return Namespace(**defaults)


def test_parser_exposes_lexicon_and_language_detection_controls():
    args = cli.build_parser().parse_args(
        [
            "render",
            "text",
            "--lexicon",
            "gold",
            "--lexicon",
            "crane",
            "--g2p-fallback",
            "goruut",
            "--lexicon-data-policy",
            "installed-only",
            "--language-detection",
            "auto",
            "--spacy",
            "off",
            "--detect-language",
            "de",
            "--detect-language",
            "en",
        ]
    )
    assert args.lexicons == ["gold", "crane"]
    assert args.g2p_fallback == "goruut"
    assert args.lexicon_data_policy == "installed-only"
    assert args.detect_languages == ["de", "en"]
    assert args.spacy == "off"
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["render", "text", "--no-lexicons", "--auto-lexicons"])


def test_config_round_trips_empty_lexicons_and_new_policies(tmp_path):
    config = ReadioConfig(
        reader=ReaderSettings(language_detection="auto", detect_languages=("de", "en")),
        languages={
            "de": LanguageSettings(
                lexicons=(),
                g2p_fallback="espeak",
                lexicon_data_policy="installed-only",
            )
        },
    )
    path = tmp_path / "readio.toml"
    path.write_text(dumps_config(config), encoding="utf-8")

    loaded = load_config(path)

    assert loaded.languages["de"].lexicons == ()
    assert loaded.languages["de"].g2p_fallback == "espeak"
    assert loaded.languages["de"].lexicon_data_policy == "installed-only"
    assert loaded.reader.language_detection == "auto"
    assert loaded.reader.detect_languages == ("de", "en")


def test_synthesis_forwards_explicit_tokenizer_and_detection_controls(monkeypatch):
    monkeypatch.setattr(
        "readio.synthesis.get_model_info", lambda *args, **kwargs: (MODEL, DISCOVERY)
    )
    resolved = resolve_synthesis(
        ReadioConfig(),
        _args(
            lexicons=[],
            no_lexicons=True,
            g2p_fallback="none",
            lexicon_data_policy="installed-only",
            language_detection="auto",
            detect_languages=["de", "en"],
        ),
    )

    assert resolved.lexicons == ()
    assert resolved.g2p_fallback == "none"
    assert resolved.lexicon_data_policy == "installed-only"
    assert resolved.language_detection == "auto"
    assert resolved.detect_languages == ("de", "en")


def test_language_detection_hint_and_lexphon_asset_guidance():
    assert language_detection_hint(
        "---\nlanguage_detection:\n  mode: auto\n  languages: [de, en]\n---\ntext"
    ) == ("auto", ("de", "en"))
    with pytest.raises(
        ModelDiscoveryError,
        match="underlying language-qualified Lexphon asset ID",
    ):
        validate_language_settings(
            "de", LanguageSettings(model=MODEL.id, lexicons=("de-de:crane",)), MODEL
        )
