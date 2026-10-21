from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest

from readio import cli
from readio.api import PipelineEvent, WorkspaceRef


@pytest.mark.parametrize("kind", ["document", "book"])
def test_bundle_run_cli_keeps_json_on_stdout_and_progress_on_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: Literal["document", "book"],
) -> None:
    source = tmp_path / "sample.txt"
    expected_variant = "voice-render/authoritative-variant"
    captured: dict[str, Any] = {}

    class FakeReadio:
        def run(
            self, received_source: Path, request: Any, *, on_progress: Any = None
        ) -> SimpleNamespace:
            captured["source"] = received_source
            captured["request"] = request
            if on_progress is not None:
                on_progress(PipelineEvent(stage="compile", current=1, total=1, message="compiled"))
            return SimpleNamespace(
                workspace=WorkspaceRef(tmp_path / "sample.ssmd-bundle", kind),
                variant=expected_variant,
                export={"path": "out.m4b"},
            )

    monkeypatch.setattr(cli.api, "Readio", FakeReadio)
    status = cli.main(
        [
            "bundle",
            "run",
            str(source),
            "--profile",
            "profile.yaml",
            "--variant",
            "friendly",
            "--export",
            "--format",
            "mp3",
            "--save-audiojobs",
            "jobs.json",
            "--json",
        ]
    )

    captured_output = capsys.readouterr()
    payload = json.loads(captured_output.out)
    assert status == 0
    assert payload["ok"] is True
    assert payload["result"]["bundle_kind"] == kind
    assert payload["result"]["variant"] == expected_variant
    assert "export" in payload["result"]["completed_stages"]
    assert "compile:" in captured_output.err
    request = captured["request"]
    assert request.render_plan.variant == "friendly"
    assert request.render.variant == "pending"
    assert request.compose.variant == "pending"
    assert request.compose.save_audiojobs == Path("jobs.json")
    assert request.export.variant == "pending"
    assert request.export.format == "mp3"


def test_bundle_convert_emits_bundle_terminology(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "story.txt"
    output = tmp_path / "story.ssmd-bundle"
    captured: dict[str, Any] = {}

    class FakeReadio:
        def create_workspace(
            self, received_source: Path, request: Any, *, on_progress: Any = None
        ) -> WorkspaceRef:
            captured["source"] = received_source
            captured["request"] = request
            return WorkspaceRef(output, "document")

    monkeypatch.setattr(cli.api, "Readio", FakeReadio)
    assert cli.main(["bundle", "convert", str(source), "--output", str(output), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["result"] == {
        "bundle": str(output),
        "bundle_kind": "document",
        "manifest": str(output / "manifest.json"),
    }
    assert captured["source"] == source
    assert captured["request"].output == output


def test_planner_controls_build_a_native_utterplan_config() -> None:
    args = cli.build_parser().parse_args(
        [
            "bundle",
            "compile",
            "sample.ssmd-bundle",
            "--language",
            "fr-FR",
            "--text-preparation",
            "ssmd",
            "--spacy",
            "off",
        ]
    )

    config = cli._planner_config(args)
    assert config is not None

    assert config.language == "fr-FR"
    assert config.text_preparation == "ssmd"
    assert config.linguistics.use_spacy is False


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


def test_help_exposes_nested_bundle_workflows_without_kind_selection() -> None:
    parser = cli.build_parser()
    help_text = parser.format_help()
    root_subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    root_commands = root_subparsers.choices
    assert "bundle" in root_commands
    assert ".ssmd-bundle" in help_text
    for old_command in ("init", "plan", "render-plan", "render", "compose", "export", "run"):
        assert old_command not in root_commands
        with pytest.raises(SystemExit):
            parser.parse_args([old_command, "sample"])

    bundle_parser = root_commands["bundle"]
    bundle_subparsers = next(
        action
        for action in bundle_parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    assert set(bundle_subparsers.choices) == {
        "convert",
        "compile",
        "render-plan",
        "render",
        "compose",
        "export",
        "run",
    }
    assert ".ssmd-bundle" in bundle_parser.format_help()
    assert "--kind" not in bundle_subparsers.choices["convert"].format_help()
    assert "--kind" not in bundle_subparsers.choices["run"].format_help()
    with pytest.raises(SystemExit):
        bundle_subparsers.choices["convert"].parse_args(["source", "--kind", "book"])
    with pytest.raises(SystemExit):
        bundle_subparsers.choices["run"].parse_args(
            ["source", "--profile", "profile.toml", "--kind", "book"]
        )
