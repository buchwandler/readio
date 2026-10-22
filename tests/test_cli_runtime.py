from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from readio import cli


@dataclass
class Distribution:
    name: str
    version: str
    location: Path
    owns_import: bool


@dataclass
class RuntimeStatus:
    importable: bool = True
    module_version: str | None = "1.24.1"
    module_path: Path | None = Path("/venv/site-packages/onnxruntime/__init__.py")
    build_device: str | None = "GPU"
    build_info: str | None = "build metadata"
    distributions: tuple[Distribution, ...] = (
        Distribution("onnxruntime-gpu", "1.24.1", Path("/venv/site-packages"), True),
    )
    imported_distribution: str | None = "onnxruntime-gpu"
    distribution_state: str = "resolved"
    available_providers: tuple[str, ...] = (
        "CUDAExecutionProvider",
        "CPUExecutionProvider",
    )
    configured_providers: tuple[str, ...] = ("cuda",)
    provider_source: str = "user_config"
    resolved_providers: tuple[str, ...] = ("CUDAExecutionProvider",)
    import_error: str | None = None
    resolution_error: str | None = None


def test_runtime_parser_accepts_all_commands_and_custom_provider_names() -> None:
    parser = cli.build_parser()
    cases = (
        (["runtime", "status"], "status", None),
        (["runtime", "status", "--json"], "status", None),
        (["runtime", "list"], "list", None),
        (["runtime", "list", "--json"], "list", None),
        (["runtime", "use", "cuda"], "use", "cuda"),
        (["runtime", "use", "cuda,cpu", "--json"], "use", "cuda,cpu"),
        (["runtime", "use", "auto"], "use", "auto"),
        (["runtime", "use", "OpenVINOExecutionProvider"], "use", "OpenVINOExecutionProvider"),
        (["runtime", "reset"], "reset", None),
    )
    for argv, command, providers in cases:
        parsed = parser.parse_args(argv)
        assert parsed.command == "runtime"
        assert parsed.runtime_command == command
        if providers is not None:
            assert parsed.providers == providers


def test_runtime_status_human_output_distinguishes_build_and_policy(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.api, "runtime_status", lambda: RuntimeStatus())

    assert cli.main(["runtime", "status"]) == 0
    output = capsys.readouterr().out
    assert "ONNX Runtime" in output
    assert "Distribution          : onnxruntime-gpu 1.24.1" in output
    assert "Build device          : GPU" in output
    assert "Available providers:" in output
    assert "Configured policy     : cuda" in output
    assert "Policy source         : user config" in output
    assert "Resolved for new sessions:" in output
    assert "CUDAExecutionProvider" in output
    assert "ACTIVE" not in output
    assert "build metadata" not in output


def test_runtime_list_human_output_labels_selected_not_active(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.api, "runtime_status", lambda: RuntimeStatus())

    assert cli.main(["runtime", "list"]) == 0
    output = capsys.readouterr().out
    assert "Installed ONNX Runtime distributions" in output
    assert "onnxruntime-gpu" in output
    assert "Providers reported by imported ORT" in output
    assert "SELECTED" in output
    assert "CUDAExecutionProvider" in output
    assert "CPUExecutionProvider" in output
    assert "ACTIVE" not in output


def test_runtime_status_json_serializes_kind_and_paths(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.api, "runtime_status", lambda: RuntimeStatus())

    assert cli.main(["runtime", "status", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)["result"]
    assert result["kind"] == "onnx"
    assert result["module_path"] == "/venv/site-packages/onnxruntime/__init__.py"
    assert result["distributions"][0]["location"] == "/venv/site-packages"
    assert result["resolved_providers"] == ["CUDAExecutionProvider"]


@pytest.mark.parametrize(
    ("status", "expected"),
    (
        (
            RuntimeStatus(
                distribution_state="not_installed",
                importable=False,
                imported_distribution=None,
                module_version=None,
                module_path=None,
                distributions=(),
                available_providers=(),
                configured_providers=(),
                resolved_providers=(),
            ),
            "not_installed",
        ),
        (
            RuntimeStatus(
                distribution_state="import_failed",
                importable=False,
                import_error="module load failed",
            ),
            "Import error: module load failed",
        ),
        (
            RuntimeStatus(distribution_state="ambiguous"),
            "multiple ONNX Runtime distributions claim",
        ),
        (
            RuntimeStatus(
                distribution_state="vendor", imported_distribution="onnxruntime-openvino"
            ),
            "onnxruntime-openvino 1.24.1",
        ),
        (
            RuntimeStatus(resolution_error="CUDA provider unavailable"),
            "Provider resolution error: CUDA provider unavailable",
        ),
    ),
)
def test_runtime_status_human_output_reports_nonstandard_states(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: RuntimeStatus,
    expected: str,
) -> None:
    monkeypatch.setattr(cli.api, "runtime_status", lambda: status)

    assert cli.main(["runtime", "status"]) == 0
    assert expected in capsys.readouterr().out


def test_runtime_use_reports_environment_override_after_saving(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured = []
    status = RuntimeStatus(
        configured_providers=("cuda",),
        provider_source="environment",
        resolved_providers=("CPUExecutionProvider",),
    )
    monkeypatch.setattr(cli.api, "set_runtime_providers", captured.append)
    monkeypatch.setattr(cli.api, "runtime_status", lambda: status)

    assert cli.main(["runtime", "use", "cuda"]) == 0
    output = capsys.readouterr().out
    assert captured == ["cuda"]
    assert "Saved ONNX provider policy: cuda" in output
    assert "Effective source is currently: ONNXVOICE_PROVIDER" in output
    assert "Effective resolved provider: CPUExecutionProvider" in output
    assert "Applies to new sessions." in output


def test_runtime_use_json_reports_post_save_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = []
    status = RuntimeStatus(configured_providers=("cuda", "cpu"))
    monkeypatch.setattr(cli.api, "set_runtime_providers", calls.append)
    monkeypatch.setattr(cli.api, "runtime_status", lambda: status)

    assert cli.main(["runtime", "use", "cuda,cpu", "--json"]) == 0
    assert calls == ["cuda,cpu"]
    result = json.loads(capsys.readouterr().out)["result"]
    assert result["kind"] == "onnx"
    assert result["configured_providers"] == ["cuda", "cpu"]


def test_runtime_reset_reports_effective_default_and_json_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    status = RuntimeStatus(provider_source="default", resolved_providers=("CPUExecutionProvider",))
    calls = []
    monkeypatch.setattr(cli.api, "reset_runtime_providers", lambda: calls.append("reset") or status)

    assert cli.main(["runtime", "reset"]) == 0
    output = capsys.readouterr().out
    assert calls == ["reset"]
    assert "Removed saved ONNX provider preference." in output
    assert "Effective source: built-in default" in output
    assert "Resolved for new sessions: CPUExecutionProvider" in output

    assert cli.main(["runtime", "reset", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)["result"]
    assert result["kind"] == "onnx"
    assert result["resolved_providers"] == ["CPUExecutionProvider"]


def test_runtime_use_preserves_voice_render_validation_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def reject(_providers: str) -> None:
        raise ValueError("CUDAExecutionProvider unavailable in imported build")

    def unexpected_status():
        pytest.fail("status should not be requested after provider validation fails")

    monkeypatch.setattr(cli.api, "set_runtime_providers", reject)
    monkeypatch.setattr(cli.api, "runtime_status", unexpected_status)

    assert cli.main(["runtime", "use", "cuda", "--json"]) == 1
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["type"] == "ValueError"
    assert "CUDAExecutionProvider unavailable" in error["message"]
