from __future__ import annotations

import importlib.metadata
import os
import re

import pytest

from readio.engines.inflectsynth import InflectSynthEngineAdapter
from readio.engines.kittensynth import KittenSynthEngineAdapter
from readio.engines.pipersynth import PiperSynthEngineAdapter
from readio.engines.pocketsynth import PocketSynthEngineAdapter
from readio.engines.pykokoro import PyKokoroEngineAdapter
from readio.engines.supertonicsynth import SupertonicSynthEngineAdapter

_ENGINE_APIS = {
    "kokoro": ("pykokoro", PyKokoroEngineAdapter, (0, 10)),
    "piper": ("pipersynth", PiperSynthEngineAdapter, (0, 2)),
    "pocket": ("pocketsynth", PocketSynthEngineAdapter, (0, 2)),
    "kitten": ("kittensynth", KittenSynthEngineAdapter, (0, 1)),
    "supertonic": ("supertonicsynth", SupertonicSynthEngineAdapter, (0, 1)),
    "inflect": ("inflectsynth", InflectSynthEngineAdapter, (0, 1)),
}


@pytest.mark.parametrize("engine", sorted(_ENGINE_APIS))
def test_released_engine_package_reports_structured_readio_api_probe(engine: str) -> None:
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
    probe = adapter_type().probe_api()
    assert probe.compatible, (
        f"{distribution} {version} API probe failed: status={probe.status}, "
        f"missing_symbols={probe.missing_symbols}, missing_methods={probe.missing_methods}, "
        f"failed_stage={probe.failed_stage}, failed_symbol={probe.failed_symbol}, "
        f"error={probe.error_type}: {probe.error_message}"
    )
    assert probe.status == "ready"
    assert probe.distribution_version == version
    assert probe.expected_api_version == 1
    assert probe.contract_source in {"explicit", "legacy_symbols"}
