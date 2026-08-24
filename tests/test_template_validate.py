import json
from pathlib import Path

from readio import cli
from readio.config import PathSettings, ReadioConfig
from readio.templates import seed_templates


def test_template_validate_all_json_uses_consumer_preflight(monkeypatch, tmp_path: Path, capsys):
    templates = tmp_path / "templates"
    seed_templates(templates)
    cfg = ReadioConfig(paths=PathSettings(templates, tmp_path / "ingest", tmp_path / "output"))
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    args = cli.build_parser().parse_args(["template", "validate", "--all", "--json"])

    assert cli._cmd_template(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert [item["name"] for item in result["templates"]] == ["briefing", "dialogue", "podcast"]
    assert all(item["consumer"]["ok"] for item in result["templates"])
