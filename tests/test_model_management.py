from __future__ import annotations

from dataclasses import fields
from types import SimpleNamespace
from typing import get_args

import pytest

from readio import engines
from readio.api import (
    EngineModelInfo,
    ModelOperationError,
    ModelState,
    ModelUpdateState,
    Readio,
    describe_error,
)
from readio.errors import CatalogError


def _artifact(
    *, installed: bool = True, status: str = "ready", revision: str | None = "rev-1"
) -> SimpleNamespace:
    return SimpleNamespace(
        engine="kitten",
        target_id="kitten-v1",
        display_name="Kitten v1",
        installed=installed,
        status=status,
        languages=("en-US",),
        revision=revision,
    )


def test_model_types_and_gateway_methods_are_public() -> None:
    assert EngineModelInfo.__dataclass_params__.frozen
    assert tuple(field.name for field in fields(EngineModelInfo)) == (
        "engine",
        "target",
        "quality",
        "language",
        "label",
        "installed",
        "available",
        "state",
        "local_version",
        "available_version",
        "update_state",
        "bytes_installed",
        "bytes_download",
        "storage_id",
        "installable",
        "updatable",
        "removable",
        "verifiable",
        "reason",
    )
    assert set(get_args(ModelState)) == {
        "missing",
        "ready",
        "outdated",
        "partial",
        "corrupt",
        "unavailable",
        "unknown",
    }
    assert set(get_args(ModelUpdateState)) == {
        "current",
        "update_available",
        "unavailable",
        "unknown",
    }
    for name in (
        "list_models",
        "model_status",
        "install_model",
        "update_model",
        "verify_model",
        "remove_model",
    ):
        assert callable(getattr(Readio, name))


def test_list_models_forwards_filters_without_running_model_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def list_models(engine: str, **kwargs: object) -> tuple[object, ...]:
        captured.update(engine=engine, **kwargs)
        return (_artifact(),)

    monkeypatch.setattr(engines, "list_engine_models", list_models)
    monkeypatch.setattr(
        engines,
        "install_engine_model",
        lambda *_args, **_kwargs: pytest.fail("inventory must not install models"),
    )

    result = Readio().list_models(
        "kitten", language="en-US", available=True, offline=True, refresh=True
    )

    assert captured == {
        "engine": "kitten",
        "language": "en-US",
        "installed": None,
        "available": True,
        "offline": True,
        "refresh": True,
    }
    assert result == (
        EngineModelInfo(
            "kitten",
            "kitten-v1",
            language="en-US",
            label="Kitten v1",
            installed=True,
            state="ready",
            local_version="rev-1",
        ),
    )


def test_list_models_preserves_filter_exclusion_and_wraps_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Readio()
    with pytest.raises(ValueError, match="mutually exclusive"):
        service.list_models("kitten", installed=True, available=True)

    failure = RuntimeError("specialist failure")
    monkeypatch.setattr(
        engines, "list_engine_models", lambda *_args, **_kwargs: (_ for _ in ()).throw(failure)
    )
    with pytest.raises(CatalogError) as caught:
        service.list_models("kitten", language="en-US", offline=True, refresh=True)
    assert caught.value.operation == "list_models"
    assert caught.value.engine == "kitten"
    assert caught.value.cause is failure
    assert caught.value.details == {
        "language": "en-US",
        "installed": None,
        "available": False,
        "offline": True,
        "refresh": True,
    }


def test_model_status_forwards_selectors_and_normalizes_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def inspect(target: str, **kwargs: object) -> object:
        captured.update(target=target, **kwargs)
        return _artifact()

    monkeypatch.setattr(engines, "inspect_engine_model", inspect)
    status = Readio().model_status("kitten", "kitten-v1", quality="high")

    assert captured == {"target": "kitten-v1", "engine": "kitten", "quality": "high"}
    assert status.target == "kitten-v1"
    assert status.quality == "high"
    assert status.local_version == "rev-1"


def test_install_and_verify_requery_when_result_has_no_descriptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def inspect(target: str, **kwargs: object) -> object:
        calls.append("status")
        return _artifact()

    monkeypatch.setattr(engines, "inspect_engine_model", inspect)
    monkeypatch.setattr(
        engines,
        "install_engine_model",
        lambda *_args, **_kwargs: SimpleNamespace(artifact=None),
    )
    monkeypatch.setattr(
        engines,
        "verify_engine_model",
        lambda *_args, **_kwargs: SimpleNamespace(artifact=None),
    )
    service = Readio()

    installed = service.install_model("kitten", "kitten-v1", quality="high", force=True)
    verified = service.verify_model("kitten", "kitten-v1", quality="high")

    assert calls == ["status", "status"]
    assert installed.state == verified.state == "ready"
    assert installed.quality == verified.quality == "high"


def test_remove_always_returns_fresh_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(engines, "remove_engine_model", lambda *_args, **_kwargs: object())

    def inspect(target: str, **kwargs: object) -> object:
        calls.append(target)
        return _artifact(installed=False, status="missing", revision=None)

    monkeypatch.setattr(engines, "inspect_engine_model", inspect)
    result = Readio().remove_model("kitten", "kitten-v1", quality="high")

    assert calls == ["kitten-v1"]
    assert result.state == "missing"
    assert result.installed is False
    assert result.quality == "high"


def test_update_is_explicitly_unsupported_and_never_force_installs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engines,
        "install_engine_model",
        lambda *_args, **_kwargs: pytest.fail("update must not alias to install"),
    )

    with pytest.raises(ModelOperationError) as caught:
        Readio().update_model("kitten", "kitten-v1")

    assert caught.value.code == "model_operation_failed"
    assert caught.value.operation == "update"
    assert caught.value.details["supported"] is False
    assert describe_error(caught.value).message == (
        "VoiceRender does not provide authoritative model update semantics."
    )


def test_mutating_operation_errors_keep_stable_messages_and_safe_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = RuntimeError("private traceback text")
    monkeypatch.setattr(
        engines,
        "install_engine_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(ModelOperationError) as caught:
        Readio().install_model("kitten", "kitten-v1", quality="high")

    error = caught.value
    assert error.cause is failure
    assert error.details == {
        "operation": "install",
        "engine": "kitten",
        "target": "kitten-v1",
        "quality": "high",
        "source": None,
        "force": False,
    }
    described = describe_error(error)
    assert described.code == "model_operation_failed"
    assert "private traceback text" not in described.message
    assert described.details == error.details
