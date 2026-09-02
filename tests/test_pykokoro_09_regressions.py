from __future__ import annotations

import json
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from readio import cli
from readio.config import LanguageSettings
from readio.models import (
    ModelDiscoveryError,
    ModelInfo,
    _pykokoro_discovery,
    validate_language_settings,
)

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


def test_discovery_api_mismatch_reports_installed_version(monkeypatch) -> None:
    fake = SimpleNamespace(__version__="0.9.0")
    monkeypatch.setitem(sys.modules, "pykokoro", fake)
    with pytest.raises(ModelDiscoveryError, match="required by Readio 0.2.0") as error:
        _pykokoro_discovery()
    assert error.value.code == "pykokoro.discovery_api_missing"
    assert error.value.installed_version == "0.9.0"


def test_model_validation_rejects_quality_and_known_lexicon() -> None:
    with pytest.raises(ModelDiscoveryError) as quality_error:
        validate_language_settings(
            "de", LanguageSettings(model=MODEL.id, quality="int8"), MODEL
        )
    assert quality_error.value.code == "pykokoro.quality_invalid"

    with pytest.raises(ModelDiscoveryError) as lexicon_error:
        validate_language_settings(
            "de", LanguageSettings(model=MODEL.id, lexicons=("silver",)), MODEL
        )
    assert lexicon_error.value.code == "pykokoro.lexicon_invalid"


def test_unknown_lexicon_inventory_is_not_rejected() -> None:
    unknown = replace(MODEL, lexicons=None)
    settings = LanguageSettings(model=MODEL.id, lexicons=("future",))
    assert validate_language_settings("de", settings, unknown) == settings


def test_experimental_model_requires_opt_in() -> None:
    experimental = replace(MODEL, status="experimental", experimental=True)
    with pytest.raises(ModelDiscoveryError) as error:
        validate_language_settings("de", LanguageSettings(model=MODEL.id), experimental)
    assert error.value.code == "pykokoro.experimental_required"
    allowed = LanguageSettings(model=MODEL.id, allow_experimental=True)
    assert validate_language_settings("de", allowed, experimental) == allowed


def test_model_voice_listing_reports_source_and_roles(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "get_model_info",
        lambda *args, **kwargs: (MODEL, SimpleNamespace(registry_source="cache", cache_fallback=False)),
    )
    args = cli.build_parser().parse_args(["voices", "list", "--model", MODEL.id, "--json"])
    assert cli._cmd_voices(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "github"
    assert payload["registry"]["source"] == "cache"
    assert payload["voices"][0]["id"] == "thorsten"
