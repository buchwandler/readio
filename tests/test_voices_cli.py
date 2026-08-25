import json
from pathlib import Path

import pytest

from readio import cli
from readio.config import PathSettings, ReadioConfig, VoiceProviderSettings


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


def test_voices_list_and_roles_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "load_config", lambda: config(tmp_path))

    assert cli._cmd_voices(cli.build_parser().parse_args(["voices", "list", "--json"])) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["provider"] == "kokoro"
    assert listed["voices"][0] == {"id": "af_sarah", "roles": ["host"]}

    assert cli._cmd_voices(cli.build_parser().parse_args(["voices", "roles", "--json"])) == 0
    roles = json.loads(capsys.readouterr().out)
    assert roles["roles"] == {"host": "af_sarah"}


def test_voices_bind_and_unbind_use_config_save(monkeypatch, tmp_path):
    cfg = config(tmp_path)
    saved = []
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(cli, "save_config", lambda updated: saved.append(updated) or Path("config.toml"))

    assert cli._cmd_voices(
        cli.build_parser().parse_args(["voices", "bind", "moderator", "am_michael"])
    ) == 0
    assert saved[-1].voices["kokoro"].roles["moderator"] == "am_michael"

    bound = saved[-1]
    monkeypatch.setattr(cli, "load_config", lambda: bound)
    assert cli._cmd_voices(
        cli.build_parser().parse_args(["voices", "unbind", "moderator"])
    ) == 0
    assert "moderator" not in saved[-1].voices["kokoro"].roles


def test_voices_bind_rejects_unknown_target(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_config", lambda: config(tmp_path))
    with pytest.raises(ValueError, match="available voices"):
        cli._cmd_voices(
            cli.build_parser().parse_args(["voices", "bind", "moderator", "missing"])
        )
