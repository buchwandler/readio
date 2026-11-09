from __future__ import annotations

import sys
from collections import deque
from typing import Any

import pytest

from readio import spacy_resources
from readio.api import SpacyModelInfo, ensure_spacy_model, inspect_spacy_model


def _info(
    *,
    installed: bool,
    spacy_version: str | None = "3.8.0",
    model_version: str | None = None,
) -> SpacyModelInfo:
    return SpacyModelInfo(
        language="en-US",
        tier="sm",
        model="en_core_web_sm",
        installed=installed,
        spacy_version=spacy_version,
        model_version=model_version,
    )


def test_public_api_exposes_stable_model_resource_functions() -> None:
    assert inspect_spacy_model is spacy_resources.inspect_spacy_model
    assert ensure_spacy_model is spacy_resources.ensure_spacy_model


def test_inspect_reports_installed_model_and_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    versions = {"spacy": "3.8.0", "en_core_web_sm": "3.8.1"}
    loaded: list[str] = []
    monkeypatch.setattr(spacy_resources, "_distribution_version", versions.get)
    monkeypatch.setattr(spacy_resources, "_load_model", loaded.append)

    info = inspect_spacy_model("en-US", "sm")

    assert info == _info(installed=True, model_version="3.8.1")
    assert info.model == "en_core_web_sm"
    assert loaded == ["en_core_web_sm"]


def test_offline_ensure_fails_without_attempting_provisioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        spacy_resources,
        "_distribution_version",
        lambda package: "3.8.0" if package == "spacy" else None,
    )
    monkeypatch.setattr(
        spacy_resources.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("offline ensure must not provision"),
    )

    with pytest.raises(RuntimeError, match="offline mode is enabled"):
        ensure_spacy_model("en-US", "sm", offline=True)


def test_ensure_reuses_an_installed_model(monkeypatch: pytest.MonkeyPatch) -> None:
    info = _info(installed=True, model_version="3.8.1")
    monkeypatch.setattr(spacy_resources, "inspect_spacy_model", lambda *_args: info)
    monkeypatch.setattr(
        spacy_resources.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("installed model must be reused"),
    )

    assert ensure_spacy_model("en-US", "sm") is info


def test_ensure_provisions_missing_model_once_and_verifies_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = deque(
        [
            _info(installed=False),
            _info(installed=True, model_version="3.8.1"),
        ]
    )
    commands: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(spacy_resources, "inspect_spacy_model", lambda *_args: reports.popleft())
    monkeypatch.setattr(
        spacy_resources.subprocess,
        "run",
        lambda *args, **kwargs: commands.append((args, kwargs)),
    )

    info = ensure_spacy_model("en-US", "sm")

    assert info.installed is True
    assert info.model_version == "3.8.1"
    assert commands == [
        (([sys.executable, "-m", "spacy", "download", "en_core_web_sm"],), {"check": True})
    ]
    assert not reports


def test_ensure_does_not_install_spacy_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spacy_resources,
        "inspect_spacy_model",
        lambda *_args: _info(installed=False, spacy_version=None),
    )
    monkeypatch.setattr(
        spacy_resources.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("spaCy itself must not be installed implicitly"),
    )

    with pytest.raises(RuntimeError, match='install Readio with the "spacy" extra'):
        ensure_spacy_model("en-US", "sm")


def test_ensure_fails_when_provisioned_model_does_not_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = deque([_info(installed=False), _info(installed=False)])
    monkeypatch.setattr(spacy_resources, "inspect_spacy_model", lambda *_args: reports.popleft())
    monkeypatch.setattr(spacy_resources.subprocess, "run", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="still unavailable after provisioning"):
        ensure_spacy_model("en-US", "sm")
