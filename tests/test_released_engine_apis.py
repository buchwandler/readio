from __future__ import annotations

import importlib.metadata
import os
import re

import pytest

from readio.engines.pipersynth import PiperSynthEngineAdapter
from readio.engines.pocketsynth import PocketSynthEngineAdapter
from readio.engines.pykokoro import PyKokoroEngineAdapter

_ENGINE_APIS = {
    "pykokoro": ("pykokoro", PyKokoroEngineAdapter, (0, 10)),
    "piper": ("pipersynth", PiperSynthEngineAdapter, (0, 2)),
    "pocket": ("pocketsynth", PocketSynthEngineAdapter, (0, 2)),
}


@pytest.mark.parametrize("engine", sorted(_ENGINE_APIS))
def test_released_engine_package_exposes_readio_request_api(engine: str) -> None:
    selected_engine = os.environ.get("READIO_TEST_ENGINE")
    if selected_engine and selected_engine != engine:
        pytest.skip(f"compatibility job selected {selected_engine}")

    distribution, adapter_type, expected_release = _ENGINE_APIS[engine]
    try:
        version = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        if selected_engine == engine:
            pytest.fail(f"compatibility job did not install {distribution}")
        pytest.skip(f"optional engine package {distribution} is not installed")

    version_match = re.match(r"^(\d+)\.(\d+)", version)
    assert version_match is not None, f"unparseable {distribution} version {version!r}"
    installed_release = (int(version_match.group(1)), int(version_match.group(2)))
    assert installed_release == expected_release, (
        f"expected {distribution} release family {expected_release}, got {version}"
    )
    assert adapter_type().compatible_api(), (
        f"{distribution} {version} does not expose Readio's strict request API"
    )
