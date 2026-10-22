from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from readio import doctor


@dataclass
class RuntimeStatus:
    importable: bool = True
    module_version: str | None = "1.24.1"
    module_path: Path | None = Path("/venv/site-packages/onnxruntime/__init__.py")
    build_device: str | None = "GPU"
    build_info: str | None = "verbose build details"
    distributions: tuple[dict[str, object], ...] = (
        {
            "name": "onnxruntime-gpu",
            "version": "1.24.1",
            "location": Path("/venv/site-packages"),
            "owns_import": True,
        },
    )
    imported_distribution: str | None = "onnxruntime-gpu"
    distribution_state: str = "resolved"
    available_providers: tuple[str, ...] = ("CUDAExecutionProvider", "CPUExecutionProvider")
    configured_providers: tuple[str, ...] = ("cuda",)
    provider_source: str = "user_config"
    resolved_providers: tuple[str, ...] = ("CUDAExecutionProvider",)
    import_error: str | None = None
    resolution_error: str | None = None


def _prepare_doctor(monkeypatch: pytest.MonkeyPatch, status: object) -> None:
    monkeypatch.setattr(doctor.voicerender, "list_engines", list)
    monkeypatch.setattr(doctor, "runtime_status", lambda: status)


def test_doctor_includes_json_safe_runtime_status_and_compact_human_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_doctor(monkeypatch, RuntimeStatus())

    report = doctor.doctor_report()
    runtime = report["runtime"]
    assert runtime["kind"] == "onnx"
    assert runtime["distribution_state"] == "resolved"
    assert runtime["module_path"] == "/venv/site-packages/onnxruntime/__init__.py"
    assert runtime["distributions"][0]["location"] == "/venv/site-packages"

    text = doctor.format_doctor_report(report)
    assert "ONNX Runtime:" in text
    assert "distribution: onnxruntime-gpu 1.24.1" in text
    assert "available: CUDAExecutionProvider, CPUExecutionProvider" in text
    assert "configured: cuda" in text
    assert "source: user config" in text
    assert "resolved: CUDAExecutionProvider" in text
    assert "verbose build details" not in text


@pytest.mark.parametrize(
    ("status", "state"),
    (
        (
            RuntimeStatus(
                importable=False,
                module_version=None,
                module_path=None,
                distributions=(),
                imported_distribution=None,
                distribution_state="not_installed",
                available_providers=(),
                configured_providers=(),
                resolved_providers=(),
            ),
            "not_installed",
        ),
        (
            RuntimeStatus(
                importable=False,
                distribution_state="import_failed",
                import_error="ImportError: broken runtime",
            ),
            "import_failed",
        ),
        (RuntimeStatus(distribution_state="ambiguous"), "ambiguous"),
        (RuntimeStatus(distribution_state="vendor"), "vendor"),
        (RuntimeStatus(resolution_error="CUDA provider unavailable"), "resolved"),
    ),
)
def test_doctor_preserves_runtime_state_and_diagnostics(
    monkeypatch: pytest.MonkeyPatch, status: RuntimeStatus, state: str
) -> None:
    _prepare_doctor(monkeypatch, status)

    runtime = doctor.doctor_report()["runtime"]
    assert runtime["distribution_state"] == state
    if status.import_error:
        assert runtime["import_error"] == status.import_error
    if status.resolution_error:
        assert runtime["resolution_error"] == status.resolution_error


def test_doctor_degrades_gracefully_when_runtime_status_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor.voicerender, "list_engines", list)

    def fail():
        raise RuntimeError("runtime probe failed\nwith verbose details")

    monkeypatch.setattr(doctor, "runtime_status", fail)
    report = doctor.doctor_report()

    assert report["runtime"] == {
        "kind": "onnx",
        "importable": False,
        "distribution_state": "import_failed",
        "import_error": "runtime probe failed",
    }
    assert "diagnostic: runtime probe failed" in doctor.format_doctor_report(report)
