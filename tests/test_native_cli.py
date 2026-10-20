from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest

from readio import cli
from readio.api import PipelineEvent, WorkspaceRef


@pytest.mark.parametrize("kind", ["document", "book"])
def test_run_cli_keeps_json_on_stdout_and_progress_on_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: Literal["document", "book"],
) -> None:
    source = tmp_path / ("sample.ssmdbook" if kind == "book" else "sample.ssmdoc")
    expected_variant = "voice-render/authoritative-variant"
    captured: dict[str, object] = {}

    class FakeReadio:
        def run(
            self, received_source: Path, request: object, *, on_progress: object = None
        ) -> SimpleNamespace:
            captured["source"] = received_source
            captured["request"] = request
            if on_progress is not None:
                on_progress(PipelineEvent(stage="compile", current=1, total=1, message="compiled"))
            return SimpleNamespace(
                workspace=WorkspaceRef(source, kind),
                variant=expected_variant,
                export={"path": "out.m4b"},
            )

    monkeypatch.setattr(cli.api, "Readio", FakeReadio)
    status = cli.main(
        [
            "run",
            str(source),
            "--profile",
            "profile.yaml",
            "--variant",
            "friendly",
            "--export",
            "--format",
            "mp3",
            "--json",
        ]
    )

    captured_output = capsys.readouterr()
    payload = json.loads(captured_output.out)
    assert status == 0
    assert payload["ok"] is True
    assert payload["result"]["workspace_kind"] == kind
    assert payload["result"]["variant"] == expected_variant
    assert "export" in payload["result"]["completed_stages"]
    assert "compile:" in captured_output.err
    request = captured["request"]
    assert request.render_plan.variant == "friendly"
    assert request.render.variant == "pending"
    assert request.compose.variant == "pending"
    assert request.export.variant == "pending"


def test_planner_controls_build_a_native_utterplan_config() -> None:
    args = cli.build_parser().parse_args(
        [
            "plan",
            "workspace.ssmdoc",
            "--language",
            "fr-FR",
            "--text-preparation",
            "ssmd",
            "--spacy",
            "off",
        ]
    )

    config = cli._planner_config(args)

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


def test_help_exposes_native_workspace_workflows() -> None:
    parser = cli.build_parser()
    help_text = parser.format_help()
    for command in ("init", "plan", "render-plan", "render", "compose", "export", "run"):
        assert command in help_text
    assert ".ssmdoc" in help_text
    assert ".ssmdbook" in help_text
