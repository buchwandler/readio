from __future__ import annotations

import importlib.resources
import os
import subprocess
import sys
from pathlib import Path

from readio.api import PUBLIC_API_VERSION, Readio


def test_public_import_surface_and_typing_marker() -> None:
    import readio.api

    assert PUBLIC_API_VERSION == 1
    assert "Readio" in readio.api.__all__
    assert "PlanRequest" in readio.api.__all__
    assert importlib.resources.files("readio").joinpath("py.typed").is_file()


def test_import_does_not_load_optional_execution_modules() -> None:
    script = """
import sys
import readio.api
forbidden = {
    'readio.cli', 'readio.spotify_cli', 'readio.spotify',
    'readio.engines.pykokoro', 'readio.engines.pipersynth',
    'pykokoro', 'pykokoro.playback', 'pipersynth', 'onnxruntime',
}
assert not forbidden.intersection(sys.modules)
"""
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        env=os.environ.copy(),
    )


def test_readio_construction_does_not_create_configuration(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "missing" / "readio.toml"
    monkeypatch.setenv("READIO_CONFIG", str(config_path))

    app = Readio()

    assert app.config is not None
    assert not config_path.exists()
