import json
from pathlib import Path

from readio import cli
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
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    args = cli.build_parser().parse_args(["ssmd", "check", str(source), "--json"])

    assert cli._cmd_ssmd(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["consumer"]["unresolved"] == []
    assert result["bindings"]["document"] == {"host": "af_bella"}
    assert result["bindings"]["defaults"]["analyst"] == "am_michael"
