from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from readio import cli, diagnostics


@pytest.mark.parametrize(
    ("argv", "expected", "verbosity"),
    [
        (["-vv", "bundle", "status", "book"], ["bundle", "status", "book"], 2),
        (["bundle", "status", "book", "-v"], ["bundle", "status", "book"], 1),
        (["doctor", "--verbose", "--verbose"], ["doctor"], 2),
        (["doctor", "-v", "-vv"], ["doctor"], 2),
        (["speak", "--", "-vv"], ["speak", "--", "-vv"], 0),
        (["speak", "hello", "--", "--verbose"], ["speak", "hello", "--", "--verbose"], 0),
    ],
)
def test_extract_global_verbosity(argv: list[str], expected: list[str], verbosity: int) -> None:
    assert cli._extract_global_verbosity(argv) == (expected, verbosity)


def test_root_help_exposes_verbose_switches() -> None:
    assert "-v, --verbose" in cli.build_parser().format_help()


def test_timed_span_uses_monotonic_elapsed_milliseconds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    diagnostics.configure_diagnostics(1)
    values = iter((10.0, 10.125))
    monkeypatch.setattr(diagnostics.time, "perf_counter", lambda: next(values))

    with diagnostics.timed_span(diagnostics.logger_for("test"), "stage", stage="render"):
        pass

    output = capsys.readouterr().err
    assert "stage.start stage=render" in output
    assert "stage.end stage=render elapsed_ms=125.0" in output


def test_failed_timed_span_records_failure_and_elapsed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    diagnostics.configure_diagnostics(1)
    values = iter((1.0, 1.25))
    monkeypatch.setattr(diagnostics.time, "perf_counter", lambda: next(values))

    with (
        pytest.raises(RuntimeError, match="broken"),
        diagnostics.timed_span(diagnostics.logger_for("test"), "command", command="doctor"),
    ):
        raise RuntimeError("broken")

    output = capsys.readouterr().err
    assert "command.failed" in output
    assert "elapsed_ms=250.0" in output
    assert "error_type=RuntimeError" in output


@pytest.mark.parametrize("verbosity", [0, 1, 2])
def test_cli_json_error_streams_and_traceback_threshold(
    verbosity: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli.api, "Readio", lambda: SimpleNamespace())

    def fail() -> dict[str, Any]:
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr(cli, "doctor_report", fail)
    argv = ["doctor", "--json"]
    if verbosity == 1:
        argv.append("-v")
    elif verbosity == 2:
        argv.insert(0, "-vv")

    assert cli.main(argv) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert payload["error"]["message"] == "unexpected failure"
    assert ("Traceback (most recent call last)" in captured.err) is (verbosity == 2)
    if verbosity:
        assert "command.failed" in captured.err
        assert "elapsed_ms=" in captured.err
    else:
        assert captured.err == ""


def test_verbose_success_keeps_json_stdout_clean(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.api, "Readio", lambda: SimpleNamespace())
    monkeypatch.setattr(cli, "doctor_report", lambda: {"healthy": True})

    assert cli.main(["doctor", "--json", "-vv"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"ok": True, "result": {"healthy": True}}
    assert "command.start" in captured.err
    assert "command.end" in captured.err
    assert "command.dispatch" in captured.err


def test_default_diagnostics_are_quiet(capsys: pytest.CaptureFixture[str]) -> None:
    diagnostics.configure_diagnostics(0)
    diagnostics.log_event(
        diagnostics.logger_for("test"), logging.INFO, "hidden.event", path=Path("x")
    )
    assert capsys.readouterr().err == ""


def test_service_stage_and_status_operations_emit_timing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from readio.api import (
        ComposeRequest,
        PlanRequest,
        RenderPlanRequest,
        RunRequest,
        StatusRequest,
        VoiceRenderRequest,
        WorkspaceRef,
    )
    from readio.api import service as service_module

    diagnostics.configure_diagnostics(1)
    monkeypatch.setattr(service_module, "plan", lambda *_args, **_kwargs: "compiled")
    monkeypatch.setattr(service_module, "inspect_status", lambda *_args: "inspected")
    service = service_module.Readio()
    workspace = WorkspaceRef(tmp_path, "book")

    assert service.plan(workspace, PlanRequest(chapters="2-4")) == "compiled"
    assert service.status(workspace, StatusRequest(chapters="2-4")) == "inspected"

    monkeypatch.setattr(service, "_run_pipeline", lambda *_args, **_kwargs: "pipeline")
    run_request = RunRequest(
        render_plan=RenderPlanRequest(profile=tmp_path / "profile.toml"),
        render=VoiceRenderRequest(variant="pending"),
        compose=ComposeRequest(variant="pending"),
    )
    assert service.run(tmp_path / "story.ssmd-bundle", run_request) == "pipeline"
    output = capsys.readouterr().err
    assert "stage.start stage=compile" in output
    assert "stage.end stage=compile" in output
    assert "stage.start stage=status" in output
    assert "stage.end stage=status" in output
    diagnostics.configure_diagnostics(0)
