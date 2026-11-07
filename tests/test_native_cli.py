from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

from readio import cli


def test_speak_and_doctor_emit_machine_readable_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli.api,
        "speak",
        lambda *_args, **_kwargs: SimpleNamespace(audio=[0.0, 0.1, -0.1], sample_rate=22050),
    )
    assert cli.main(["speak", "hello", "world", "--no-play", "--json"]) == 0
    speech = json.loads(capsys.readouterr().out)
    assert speech["result"]["sample_rate"] == 22050
    assert speech["result"]["frames"] == 3

    monkeypatch.setattr(
        cli, "doctor_report", lambda: {"readio": "test", "packages": {}, "engines": []}
    )
    assert cli.main(["doctor", "--json"]) == 0
    health = json.loads(capsys.readouterr().out)
    assert health["result"]["readio"] == "test"


def test_help_exposes_unified_workflows_and_removes_stage_runner_namespace() -> None:
    parser = cli.build_parser()
    help_text = parser.format_help()
    root_subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    root_commands = root_subparsers.choices
    assert {"render", "status", "speak", "intent"} <= set(root_commands)
    assert "bundle" not in root_commands
    assert "stage" not in help_text.casefold()
    for old_command in ("convert", "compile", "render-plan", "compose", "export", "run"):
        assert old_command not in root_commands

    render_help = root_commands["render"].format_help()
    for option in ("--bundle", "--dry-run", "--replace-output", "--rebuild-from"):
        assert option in render_help

    with pytest.raises(SystemExit):
        parser.parse_args(["bundle", "run", "source"])


def test_render_controls_are_workflow_scoped() -> None:
    args = cli.build_parser().parse_args(
        [
            "render",
            "novel.epub",
            "--profile",
            "profile.toml",
            "--bundle",
            "novel.ssmd-bundle",
            "--rebuild-from",
            "render-plan",
            "--replace-output",
        ]
    )
    assert args.command == "render"
    assert args.bundle.name == "novel.ssmd-bundle"
    assert args.rebuild_from == "render-plan"
    assert args.replace_output is True
