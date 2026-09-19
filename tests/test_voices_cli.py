from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from readio import cli
from readio.config import PathSettings, ReadioConfig, VoiceProviderSettings
from readio.voices import VoiceCatalogEntry


def config(tmp_path: Path) -> ReadioConfig:
    return ReadioConfig(
        paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "out"),
        voices={
            "kokoro": VoiceProviderSettings(
                ids=("af_sarah", "am_michael"),
                roles={"host": "af_sarah"},
            )
        },
    )


def catalog_entry() -> VoiceCatalogEntry:
    return VoiceCatalogEntry(
        selector="de-1",
        number=1,
        id="martin",
        gender="male",
        language="de",
        locale="de",
        language_label="German",
        model="de-model",
        source="github",
        default=True,
        status="ready",
        experimental=False,
        runtime_available=True,
    )


def patch_catalog(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "discover_voice_catalog",
        lambda **_: (
            (catalog_entry(),),
            SimpleNamespace(registry_source="cache", cache_fallback=False),
        ),
    )


def test_voices_list_and_show_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "load_config", lambda: config(tmp_path))
    patch_catalog(monkeypatch)

    assert (
        cli._cmd_voices(cli.build_parser().parse_args(["voices", "list", "--lang", "de", "--json"]))
        == 0
    )
    listed = json.loads(capsys.readouterr().out)
    assert listed["filters"]["language"] == "de"
    assert listed["voices"][0]["selector"] == "de-1"
    assert listed["voices"][0]["id"] == "martin"

    assert cli._cmd_voices(cli.build_parser().parse_args(["voices", "show", "de-1", "--json"])) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["voice"]["selector"] == "de-1"
    assert shown["voice"]["model"] == "de-model"


def test_pipersynth_alias_filters_canonical_piper(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "load_config", lambda: config(tmp_path))
    piper_entry = VoiceCatalogEntry(
        selector="de_DE-thorsten-medium",
        number=1,
        id="de_DE-thorsten-medium",
        gender="unknown",
        language="de",
        locale="de-DE",
        language_label="DE",
        model="de_DE-thorsten-medium",
        source="pipersynth",
        default=False,
        status="ready",
        experimental=False,
        runtime_available=True,
        engine="piper",
    )
    monkeypatch.setattr(
        cli,
        "discover_voice_catalog",
        lambda **kwargs: (
            (piper_entry,),
            SimpleNamespace(registry_source="engine-adapters", cache_fallback=False),
        ),
    )
    args = cli.build_parser().parse_args(["voices", "list", "--engine", "pipersynth", "--json"])
    assert cli._cmd_voices(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["filters"]["engine"] == "piper"
    assert payload["voices"][0]["engine"] == "piper"


def test_roles_bind_and_unbind_use_config_save(monkeypatch, tmp_path):
    cfg = config(tmp_path)
    saved = []
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(
        cli, "save_config", lambda updated: saved.append(updated) or Path("config.toml")
    )

    assert (
        cli._cmd_roles(cli.build_parser().parse_args(["roles", "bind", "moderator", "new_voice"]))
        == 0
    )
    assert saved[-1].voices["kokoro"].roles["moderator"] == "new_voice"
    assert "new_voice" in saved[-1].voices["kokoro"].ids

    bound = saved[-1]
    monkeypatch.setattr(cli, "load_config", lambda: bound)
    assert cli._cmd_roles(cli.build_parser().parse_args(["roles", "unbind", "moderator"])) == 0
    assert "moderator" not in saved[-1].voices["kokoro"].roles


def test_legacy_roles_alias_emits_warning(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "load_config", lambda: config(tmp_path))
    assert cli._cmd_voices(cli.build_parser().parse_args(["voices", "roles", "--json"])) == 0
    assert "deprecated" in capsys.readouterr().err
