from __future__ import annotations

import json
from pathlib import Path

import pytest

from readio import cli
from readio.config import (
    CONFIG_TEMPLATE,
    init_settings,
    load_settings,
    merge_settings,
    validate_settings,
)


def test_default_template_is_valid_and_carries_documented_sections(tmp_path: Path) -> None:
    path = init_settings(tmp_path / "readio.toml")
    assert path.read_text(encoding="utf-8") == CONFIG_TEMPLATE
    settings = load_settings(path)
    assert settings["format"] == "readio-settings"
    assert settings["document"]["convert"]["sequence_fallback_mode"] == "preserve"
    assert settings["compose"]["sample_rate"] == 24000
    assert settings["export"]["bitrate"] == "192k"


def test_init_settings_requires_force_to_replace_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "readio.toml"
    path.write_text("user data", encoding="utf-8")
    with pytest.raises(FileExistsError):
        init_settings(path)
    assert path.read_text(encoding="utf-8") == "user data"
    init_settings(path, force=True)
    assert load_settings(path)["schema_version"] == 1


def test_settings_validator_rejects_unknown_fields_and_invalid_types() -> None:
    with pytest.raises(ValueError, match="unknown setting"):
        validate_settings({"format": "readio-settings", "schema_version": 1, "project": {}})
    with pytest.raises(TypeError, match="boolean"):
        validate_settings(
            {
                "format": "readio-settings",
                "schema_version": 1,
                "render": {"force": "yes"},
            }
        )
    with pytest.raises(ValueError, match="one of"):
        validate_settings(
            {
                "format": "readio-settings",
                "schema_version": 1,
                "document": {"plan": {"overlap_mode": "guess"}},
            }
        )


def test_cli_overrides_deep_merge_over_config_without_mutating_settings() -> None:
    settings = {"compose": {"channels": 1, "sample_rate": 24000}, "render": {"force": False}}
    overrides = {"compose": {"channels": 2}}
    assert merge_settings(settings, overrides) == {
        "compose": {"channels": 2, "sample_rate": 24000},
        "render": {"force": False},
    }
    assert settings["compose"]["channels"] == 1
    assert overrides == {"compose": {"channels": 2}}


def test_config_cli_init_show_validate_and_structured_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "user.toml"
    assert cli.main(["config", "init", "-o", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "result": {"path": str(path)}}
    assert cli.main(["config", "validate", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["result"] == {"path": str(path), "valid": True}
    assert cli.main(["config", "show", str(path), "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["result"]["settings"]["format"] == "readio-settings"

    broken = tmp_path / "broken.toml"
    broken.write_text("not = [valid", encoding="utf-8")
    assert cli.main(["config", "validate", str(broken), "--json"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["ok"] is False
    assert error["error"]["type"] == "ConfigurationError"
