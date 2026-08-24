from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from readio.config import PathSettings, ReadioConfig, save_config
from readio.templates import seed_templates


@dataclass(frozen=True, slots=True)
class TestWorkspace:
    root: Path
    config: Path
    templates: Path
    ingest: Path
    output: Path


@pytest.fixture
def readio_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestWorkspace:
    config = tmp_path / "config" / "readio.toml"
    templates = tmp_path / "templates"
    ingest = tmp_path / "ingest"
    output = tmp_path / "output"
    cfg = ReadioConfig(paths=PathSettings(templates, ingest, output))
    save_config(cfg, config)
    seed_templates(templates)
    monkeypatch.setenv("READIO_CONFIG", str(config))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    return TestWorkspace(tmp_path, config, templates, ingest, output)
