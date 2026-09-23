from __future__ import annotations

import json
from dataclasses import replace

import pytest

from readio import cli
from readio.api.configuration import ConfigurationService
from readio.config import LanguageSettings, ReadioConfig


def test_defaults_set_autocompletes_and_saves_profile(monkeypatch, capsys, tmp_path) -> None:
    saved: list[ReadioConfig] = []
    monkeypatch.setattr(cli, "load_config", lambda: ReadioConfig())
    monkeypatch.setattr(
        ConfigurationService,
        "path",
        lambda self: tmp_path / "config.toml",
    )
    monkeypatch.setattr(
        ConfigurationService,
        "_resolve_profile",
        lambda self, language, settings, *, discovery: (
            language, replace(settings, source="github", voice="thorsten", quality="fp32")
        ),
    )
    monkeypatch.setattr(
        "readio.config.save_config",
        lambda cfg, path=None: saved.append(cfg) or tmp_path / "config.toml",
    )

    args = cli.build_parser().parse_args(
        ["defaults", "set", "de", "--model", "de-thorsten", "--lexicon", "crane", "--json"]
    )
    assert cli._cmd_defaults(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["profile"]["source"] == "github"
    assert payload["profile"]["voice"] == "thorsten"
    assert payload["profile"]["quality"] == "fp32"
    assert payload["profile"]["lexicons"] == ["crane"]
    assert saved[-1].languages["de"].voice == "thorsten"


def test_defaults_show_reports_base_fallback(monkeypatch, capsys) -> None:
    cfg = ReadioConfig(languages={"de": LanguageSettings(model="de-thorsten", voice="thorsten")})
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    args = cli.build_parser().parse_args(["defaults", "show", "de-at", "--json"])

    assert cli._cmd_defaults(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["language"] == "de-at"
    assert payload["matched_key"] == "de"
    assert payload["match"] == "base"


def test_defaults_set_lexicon_options_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            ["defaults", "set", "de", "--lexicon", "crane", "--no-lexicons"]
        )
