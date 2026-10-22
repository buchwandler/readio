from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from readio import cli
from readio.api import PipelineEvent, WorkspaceRef
from readio.intent import create_document_intent


def test_one_off_render_resolves_document_options_and_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "article.md"
    source.write_text("source", encoding="utf-8")
    captured: dict[str, Any] = {}

    class FakeReadio:
        def render_intent(
            self,
            intent: Any,
            options: Any,
            *,
            on_progress: Any = None,
            **overrides: Any,
        ) -> SimpleNamespace:
            captured.update(intent=intent, options=options, overrides=overrides)
            if on_progress is not None:
                on_progress(PipelineEvent(stage="compile", current=1, total=1, message="compiled"))
            return SimpleNamespace(
                workspace=WorkspaceRef(tmp_path / "article.ssmd-bundle", "document"),
                kind="document",
                source=source,
                profile=Path("profile.toml"),
                variant="utterplan/exact-id",
                output=tmp_path / "article.flac",
                converted=True,
                elapsed_seconds=1.25,
            )

    monkeypatch.setattr(cli, "inspect_source_kind", lambda _source: "document")
    monkeypatch.setattr(cli.api, "Readio", FakeReadio)
    status = cli.main(
        [
            "render",
            str(source),
            "--profile",
            "profile.toml",
            "--format",
            "flac",
            "--output",
            str(tmp_path / "article.flac"),
            "--force-stage",
            "render-plan",
            "--restart-from",
            "export",
            "--json",
        ]
    )

    captured_output = capsys.readouterr()
    payload = json.loads(captured_output.out)
    assert status == 0
    assert payload["ok"] is True
    assert payload["result"]["variant"] == "utterplan/exact-id"
    assert captured["intent"].kind == "document"
    assert captured["intent"].output.format == "flac"
    assert captured["intent"].output.path == tmp_path / "article.flac"
    assert captured["options"].force_stages == ("render_plan",)
    assert captured["options"].restart_from == "export"
    assert captured["overrides"]["source"] == source
    assert captured["overrides"]["output_format"] == "flac"
    assert "compiled" in captured_output.err


def test_persistent_render_dry_run_applies_cli_overrides_without_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = tmp_path / "article.readio.toml"
    intent = create_document_intent("article.md", profile="profile.toml")
    cli.api.save_render_intent(intent, config)
    captured: dict[str, Any] = {}

    class FakeReadio:
        def plan_render_intent(
            self,
            received_intent: Any,
            options: Any,
            **overrides: Any,
        ) -> SimpleNamespace:
            captured.update(intent=received_intent, options=options, overrides=overrides)
            resolved = SimpleNamespace(
                source=Path("override.md"),
                kind="document",
                bundle=Path("override.ssmd-bundle"),
                profile=Path("override.renderprofile.toml"),
                output=Path("override.wav"),
            )
            return SimpleNamespace(
                resolved=resolved,
                status=None,
                stages=("convert", "compile", "render-plan", "render", "compose", "export"),
                convert_required=True,
                output_exists=False,
            )

        def render_intent(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("--dry-run must not execute the pipeline")

    monkeypatch.setattr(cli.api, "Readio", FakeReadio)
    assert (
        cli.main(
            [
                "render",
                "override.md",
                "--config",
                str(config),
                "--profile",
                "override.renderprofile.toml",
                "--format",
                "wav",
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"]["convert_required"] is True
    assert captured["overrides"]["config_path"] == config
    assert captured["overrides"]["source"] == Path("override.md")
    assert captured["overrides"]["profile"] == Path("override.renderprofile.toml")
    assert captured["overrides"]["output_format"] == "wav"


def test_status_config_reports_conversion_needed_without_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = tmp_path / "article.readio.toml"
    cli.api.save_render_intent(
        create_document_intent("article.md", profile="profile.toml"),
        config,
    )
    resolved = SimpleNamespace(
        source=Path("article.md"),
        bundle=tmp_path / "article.ssmd-bundle",
        profile=Path("profile.toml"),
        output=Path("article.mp3"),
        chapters="all",
        intent=SimpleNamespace(voice=SimpleNamespace(variant=None)),
    )

    class FakeReadio:
        def resolve_intent(self, _intent: Any, **_kwargs: Any) -> Any:
            return resolved

        def status(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("status must not inspect a missing bundle")

    monkeypatch.setattr(cli.api, "Readio", FakeReadio)
    assert cli.main(["status", "--config", str(config), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"]["convert_required"] is True
    assert payload["result"]["resolved"]["bundle"] == str(tmp_path / "article.ssmd-bundle")


def test_render_requires_source_or_config_and_profile_for_one_off(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli.api, "Readio", lambda: object())
    assert cli.main(["render"]) == 2
    assert "provide SOURCE or --config PATH" in capsys.readouterr().err
    assert cli.main(["render", "article.md"]) == 2
    assert "--profile is required" in capsys.readouterr().err


def test_render_help_exposes_execution_controls(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["render", "--help"])
    assert error.value.code == 0
    help_text = capsys.readouterr().out
    for option in (
        "--config",
        "--profile",
        "--dry-run",
        "--force-stage",
        "--restart-from",
        "--ffmpeg",
        "--ffprobe",
    ):
        assert option in help_text
