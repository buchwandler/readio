from __future__ import annotations

import json

import pytest

from readio import cli
from readio.api import UNSET
from readio.api.configuration import ConfigurationService
from readio.config import LanguageSettings, ReadioConfig


def _capture_update(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: ReadioConfig())
    monkeypatch.setattr(
        ConfigurationService,
        "language_profiles",
        lambda _self: pytest.fail("defaults set must not load profiles for merging"),
    )
    captured: dict[str, object] = {}

    def update(self, language, patch, *, validate_runtime, discovery):
        captured["language"] = language
        captured["patch"] = patch
        captured["validate_runtime"] = validate_runtime
        captured["discovery"] = discovery
        return LanguageSettings(
            model="de-thorsten",
            source="github",
            quality="fp32",
            voice="thorsten",
            lexicons=("crane",),
        )

    monkeypatch.setattr(ConfigurationService, "update_language_profile", update)
    return captured


def test_defaults_set_builds_a_patch_and_renders_returned_settings(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    captured = _capture_update(monkeypatch)
    args = cli.build_parser().parse_args(
        [
            "defaults",
            "set",
            "de",
            "--model",
            "de-thorsten",
            "--model-source",
            "github",
            "--quality",
            "fp32",
            "--voice",
            "thorsten",
            "--lexicon",
            "crane",
            "--json",
        ]
    )

    assert cli._cmd_defaults(args) == 0
    payload = json.loads(capsys.readouterr().out)
    patch = captured["patch"]
    assert payload["profile"]["source"] == "github"
    assert payload["profile"]["voice"] == "thorsten"
    assert payload["profile"]["quality"] == "fp32"
    assert payload["profile"]["lexicons"] == ["crane"]
    assert patch.model == "de-thorsten"
    assert patch.source == "github"
    assert patch.quality == "fp32"
    assert patch.voice == "thorsten"
    assert patch.lexicons == ("crane",)
    assert patch.allow_experimental is UNSET
    assert captured["language"] == "de"
    assert captured["validate_runtime"] is True


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ([], UNSET),
        (["--auto-lexicons"], None),
        (["--no-lexicons"], ()),
        (["--lexicon", "a", "--lexicon", "b"], ("a", "b")),
    ],
)
def test_defaults_set_preserves_lexicon_patch_tri_state(
    monkeypatch: pytest.MonkeyPatch, flags: list[str], expected: object
) -> None:
    captured = _capture_update(monkeypatch)
    args = cli.build_parser().parse_args(["defaults", "set", "de", *flags, "--json"])

    assert cli._cmd_defaults(args) == 0

    patch = captured["patch"]
    if expected is UNSET:
        assert patch.lexicons is UNSET
    else:
        assert patch.lexicons == expected


@pytest.mark.parametrize(
    ("flags", "expected"),
    [([], UNSET), (["--allow-experimental"], True), (["--no-allow-experimental"], False)],
)
def test_defaults_set_distinguishes_experimental_patch_values(
    monkeypatch: pytest.MonkeyPatch, flags: list[str], expected: object
) -> None:
    captured = _capture_update(monkeypatch)
    args = cli.build_parser().parse_args(["defaults", "set", "de", *flags, "--json"])

    assert cli._cmd_defaults(args) == 0

    patch = captured["patch"]
    assert patch.allow_experimental is expected


def test_defaults_show_reports_base_fallback(monkeypatch, capsys) -> None:
    cfg = ReadioConfig(languages={"de": LanguageSettings(model="de-thorsten", voice="thorsten")})
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: cfg)
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
