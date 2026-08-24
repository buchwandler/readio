import json
from pathlib import Path

from readio import cli
from readio.config import PathSettings, ReadioConfig


def test_doctor_reports_ssmd_executable_provider_roles_and_paths(
    monkeypatch, tmp_path: Path, capsys
):
    cfg = ReadioConfig(
        paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "output")
    )
    monkeypatch.setattr(cli, "load_config", lambda path=None: cfg)
    monkeypatch.setattr(cli, "config_path", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/bin/ssmd" if name == "ssmd" else None)
    assert cli._cmd_doctor(None) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ssmd"]["executable"] == "/bin/ssmd"
    assert result["ssmd"]["voice_provider"] == "kokoro"
    assert result["voices"]["roles"]["analyst"] == "am_michael"
    assert result["paths"]["output"]["exists"] is False


def test_doctor_does_not_create_missing_directories(monkeypatch, tmp_path: Path, capsys):
    paths = PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "output")
    cfg = ReadioConfig(paths=paths)
    monkeypatch.setattr(cli, "load_config", lambda path=None: cfg)
    monkeypatch.setattr(cli, "config_path", lambda: tmp_path / "config.toml")
    cli._cmd_doctor(None)
    capsys.readouterr()
    assert not paths.templates.exists()
    assert not paths.ingest.exists()
    assert not paths.output.exists()
