import json
from pathlib import Path

import pytest

from readio.config import default_config
from readio.ssmd_authoring import (
    SSMDAuthoringError,
    build_ssmd_config,
    run_ssmd_json,
 )


def test_ssmd_authoring_config_contains_selected_provider_inventory_and_analyst():
    bridge = build_ssmd_config(default_config())
    assert bridge["authoring"]["default_voice_provider"] == "kokoro"
    assert "am_michael" in bridge["voice_inventory"]["kokoro"]
    assert bridge["voice_bindings"]["kokoro"]["analyst"] == "am_michael"


def test_ssmd_authoring_command_uses_root_json_and_config_options(monkeypatch, tmp_path: Path):
    calls = []
    monkeypatch.setattr("readio.ssmd_authoring.executable", lambda: "/bin/ssmd")

    class Result:
        returncode = 0
        stdout = json.dumps({"ok": True})
        stderr = ""

    monkeypatch.setattr(
        "readio.ssmd_authoring.subprocess.run",
        lambda command, **kwargs: calls.append(command) or Result(),
    )
    run_ssmd_json(
        ["create", "source.ssmd", "-o", "prepared.ssmd", "--fail-on-warn"],
        config_path=tmp_path / "c.yaml",
    )
    assert calls[0][:5] == ["/bin/ssmd", "--json", "--config", str(tmp_path / "c.yaml"), "create"]


def test_ssmd_authoring_invalid_json_is_actionable(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("readio.ssmd_authoring.executable", lambda: "/bin/ssmd")
    monkeypatch.setattr(
        "readio.ssmd_authoring.subprocess.run",
        lambda *args, **kwargs: type(
            "Result", (), {"returncode": 0, "stdout": "nope", "stderr": ""}
        )(),
    )
    with pytest.raises(SSMDAuthoringError, match="invalid JSON"):
        run_ssmd_json(["lint", "file.ssmd"], config_path=tmp_path / "c.yaml")
