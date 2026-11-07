from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from readio import cli
from readio.api import WorkflowEvent, WorkflowPlan
from readio.intent import create_document_intent


def _configure_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    formats = {
        name: SimpleNamespace(extension=f".{name}") for name in ("mp3", "wav", "flac", "m4b")
    }
    monkeypatch.setattr("readio.intent_resolver.supported_formats", lambda: formats)
    monkeypatch.setattr("readio.intent_resolver.inspect_source_kind", lambda _source: "document")
    monkeypatch.setattr("readio.profiles.validate_profile", lambda _path: object())
    monkeypatch.setattr(cli, "inspect_source_kind", lambda _source: "document")


def test_one_off_render_uses_unified_request_and_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "article.md"
    source.write_text("source", encoding="utf-8")
    profile = tmp_path / "profile.toml"
    profile.write_text("profile", encoding="utf-8")
    _configure_resolver(monkeypatch)
    captured: dict[str, Any] = {}

    class FakeReadio:
        def render(self, request: Any, *, on_progress: Any = None) -> SimpleNamespace:
            captured["request"] = request
            if on_progress is not None:
                on_progress(
                    WorkflowEvent(
                        stage="compile", kind="progress", current=1, total=1, message="compiled"
                    )
                )
            return SimpleNamespace(
                workspace=None,
                workspace_mode="temporary",
                variant="utterplan/exact-id",
                output=tmp_path / "article.flac",
            )

    monkeypatch.setattr(cli.api, "Readio", FakeReadio)
    status = cli.main(
        [
            "render",
            str(source),
            "--profile",
            str(profile),
            "--format",
            "flac",
            "--output",
            str(tmp_path / "article.flac"),
            "--replace-output",
            "--rebuild-from",
            "render-plan",
            "--json",
        ]
    )

    captured_output = capsys.readouterr()
    payload = json.loads(captured_output.out)
    request = captured["request"]
    assert status == 0
    assert payload["ok"] is True
    assert payload["result"]["workspace_mode"] == "temporary"
    assert request.source == source.resolve()
    assert request.profile == profile.resolve()
    assert request.format == "flac"
    assert request.output == (tmp_path / "article.flac").resolve()
    assert request.bundle is None
    assert request.rebuild_from == "render_plan"
    assert request.replace_output is True
    assert request.composition.options is not None
    assert "compiled" in captured_output.err


def test_persistent_render_dry_run_applies_cli_overrides_without_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "article.readio.toml"
    intent = create_document_intent("article.md", profile="profile.toml")
    cli.api.save_render_intent(intent, config)
    (tmp_path / "override.md").write_text("source", encoding="utf-8")
    (tmp_path / "override.toml").write_text("profile", encoding="utf-8")
    _configure_resolver(monkeypatch)
    captured: dict[str, Any] = {}

    class FakeReadio:
        def inspect(self, request: Any) -> WorkflowPlan:
            captured["request"] = request
            return WorkflowPlan(
                source=request.source,
                workspace=request.bundle,
                workspace_mode="persistent",
                variant=request.variant,
                output=request.output,
                steps=(),
            )

        def render(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("--dry-run must not execute the workflow")

    monkeypatch.setattr(cli.api, "Readio", FakeReadio)
    assert (
        cli.main(
            [
                "render",
                "override.md",
                "--config",
                str(config),
                "--profile",
                "override.toml",
                "--bundle",
                "persistent.ssmd-bundle",
                "--format",
                "wav",
                "--output",
                "override.wav",
                "--replace-output",
                "--rebuild-from",
                "export",
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    request = captured["request"]
    assert payload["ok"] is True
    assert request.source == (tmp_path / "override.md").resolve()
    assert request.profile == (tmp_path / "override.toml").resolve()
    assert request.bundle == (tmp_path / "persistent.ssmd-bundle").resolve()
    assert request.format == "wav"
    assert request.rebuild_from == "export"
    assert request.replace_output is True
    assert payload["result"]["workspace_mode"] == "persistent"


def test_status_inspects_the_same_workflow_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "story.md"
    source.write_text("story", encoding="utf-8")
    profile = tmp_path / "profile.toml"
    profile.write_text("profile", encoding="utf-8")
    _configure_resolver(monkeypatch)
    captured: dict[str, Any] = {}

    class FakeReadio:
        def inspect(self, request: Any) -> WorkflowPlan:
            captured["request"] = request
            return WorkflowPlan(
                source=request.source,
                workspace=None,
                workspace_mode="temporary",
                variant=None,
                output=request.output,
                steps=(),
            )

    monkeypatch.setattr(cli.api, "Readio", FakeReadio)
    assert cli.main(["status", str(source), "--profile", str(profile), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"]["workspace_mode"] == "temporary"
    assert captured["request"].source == source.resolve()


def test_render_requires_source_or_config_and_profile_for_one_off(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli.api, "Readio", lambda: object())
    assert cli.main(["render"]) == 2
    assert "provide SOURCE or --config PATH" in capsys.readouterr().err
    assert cli.main(["render", "article.md"]) == 2
    assert "--profile is required" in capsys.readouterr().err


def test_cli_removes_stage_runner_and_exposes_workflow_safety_controls() -> None:
    parser = cli.build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, pytest.importorskip("argparse")._SubParsersAction)
    )
    assert "bundle" not in subparsers.choices
    render = subparsers.choices["render"]
    help_text = render.format_help()
    for option in ("--config", "--profile", "--dry-run", "--replace-output", "--rebuild-from"):
        assert option in help_text
    for old_option in ("--force-stage", "--restart-from", "--reconvert", "--reset-stale-output"):
        assert old_option not in help_text
