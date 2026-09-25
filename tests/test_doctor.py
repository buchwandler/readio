import json
from pathlib import Path

from readio import cli
from readio.config import PathSettings, ReadioConfig


def test_doctor_reports_ssmd_executable_provider_roles_paths_and_audio_formats(
    monkeypatch, tmp_path: Path, capsys
):
    cfg = ReadioConfig(
        paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "output")
    )
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: cfg)
    monkeypatch.setattr(
        "readio.config.config_path",
        lambda: tmp_path / "config.toml",
    )
    assert cli._cmd_doctor(None) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["voice_provider"] == "kokoro"
    assert result["config_path"] == str(tmp_path / "config.toml")
    paths = {item["name"]: item for item in result["paths"]}
    assert paths["output"]["exists"] is False
    assert {item["id"] for item in result["audio_formats"]} == {
        "wav",
        "mp3",
        "m4a",
        "ogg",
    }

    assert {"pykokoro", "piper", "pocket"}.issubset({item["id"] for item in result["engines"]})


def test_doctor_does_not_create_missing_directories(monkeypatch, tmp_path: Path, capsys):
    paths = PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "output")
    cfg = ReadioConfig(paths=paths)
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: cfg)
    monkeypatch.setattr(
        "readio.config.config_path",
        lambda: tmp_path / "config.toml",
    )
    cli._cmd_doctor(None)
    capsys.readouterr()
    assert not paths.templates.exists()
    assert not paths.ingest.exists()
    assert not paths.output.exists()
