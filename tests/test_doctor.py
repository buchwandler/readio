from __future__ import annotations

from readio.doctor import check_ssmdconvert, run_doctor


def test_doctor_reports_ssmdconvert_as_a_core_dependency() -> None:
    status = check_ssmdconvert()
    assert status["available"] is True
    assert status["version"] is not None


def _incompatible_probe_status():
    return {
        "adapter": True,
        "package": True,
        "version": "0.1.1",
        "module_version": "0.1.0",
        "module_path": "/tmp/kittensynth/__init__.py",
        "request_api_version": 2,
        "expected_request_api_version": 1,
        "contract_source": "explicit",
        "api_compatible": False,
        "status": "api_version_incompatible",
        "missing_symbols": ("discover_models",),
        "missing_methods": (),
        "failed_stage": None,
        "failed_symbol": None,
        "error_type": None,
        "error_message": None,
        "warnings": ("Imported module version differs from distribution metadata.",),
    }


def test_doctor_text_reports_structured_engine_probe(monkeypatch) -> None:
    monkeypatch.setattr(
        "readio.doctor.engine_status", lambda: {"kitten": _incompatible_probe_status()}
    )

    report = run_doctor()

    assert "module version: 0.1.0" in report
    assert "module path: /tmp/kittensynth/__init__.py" in report
    assert "request API: 2" in report
    assert "contract: explicit" in report
    assert "request API compatible: no" in report
    assert "missing API: discover_models" in report
    assert "status: api_version_incompatible" in report
    assert "warning: Imported module version differs from distribution metadata." in report


def test_doctor_api_json_preserves_structured_probe_fields(monkeypatch) -> None:
    from readio.api import Readio
    from readio.api import diagnostics as diagnostics_module
    from readio.config import ReadioConfig

    monkeypatch.setattr(
        diagnostics_module,
        "engine_status",
        lambda: {"kitten": _incompatible_probe_status()},
    )

    engine = Readio(ReadioConfig()).diagnostics.engines()[0]
    payload = engine.to_dict()

    assert payload["status"] == "api_version_incompatible"
    assert payload["adapter_available"] is True
    assert payload["package_available"] is True
    assert payload["version"] == "0.1.1"
    assert payload["module_version"] == "0.1.0"
    assert payload["request_api_version"] == 2
    assert payload["expected_request_api_version"] == 1
    assert payload["contract_source"] == "explicit"
    assert payload["api_compatible"] is False
    assert payload["missing_symbols"] == ["discover_models"]
