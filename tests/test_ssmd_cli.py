from __future__ import annotations

import json
from pathlib import Path

import pytest

from readio import cli
from readio.api import Readio, VoiceResolutionError, default_config
from readio.config import PathSettings, ReadioConfig


def test_ssmd_check_json_reports_consumer_and_bindings(monkeypatch, tmp_path: Path, capsys):
    source = tmp_path / "episode.ssmd"
    source.write_text(
        '---\nvoice_bindings:\n  kokoro:\n    host: af_bella\n---\n<div voice="host">Hello.</div>',
        encoding="utf-8",
    )
    cfg = ReadioConfig(
        paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "out")
    )
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: cfg)
    args = cli.build_parser().parse_args(["ssmd", "check", str(source), "--json"])

    assert cli._cmd_ssmd(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["consumer"]["unresolved"] == []
    assert result["bindings"]["document"] == {"host": "af_bella"}
    assert result["bindings"]["defaults"]["analyst"] == "am_michael"


def test_ssmd_cli_and_api_raise_identical_public_errors(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "unresolved.ssmd"
    source.write_text('<div voice="speaker">Hello.</div>\n', encoding="utf-8")
    app = Readio(default_config())
    monkeypatch.setattr(cli, "_api_for", lambda _args: app)
    args = cli.build_parser().parse_args(["ssmd", "check", str(source)])
    synthesis = cli._synthesis_request_from_args(args, default_language=app.config.reader.lang)

    with pytest.raises(VoiceResolutionError) as api_error:
        app.ssmd.validate(source, synthesis=synthesis)
    with pytest.raises(VoiceResolutionError) as cli_error:
        cli._cmd_ssmd(args)

    assert cli_error.value.code == api_error.value.code
    assert cli_error.value.message == api_error.value.message
    assert cli_error.value.details == api_error.value.details
    assert cli_error.value.source_path == api_error.value.source_path
