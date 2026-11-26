from __future__ import annotations

from types import SimpleNamespace

import pytest

from readio.api.types import EngineModelInfo, ModelState
from readio.errors import CatalogError
from readio.model_catalog import model_info, model_state, model_update_state


@pytest.mark.parametrize(
    ("status", "installed", "expected"),
    [
        ("ready", True, "ready"),
        ("missing", False, "missing"),
        ("outdated", True, "outdated"),
        ("partial", True, "partial"),
        ("corrupt", True, "corrupt"),
        ("unavailable", False, "unavailable"),
        ("provider-specific", True, "unknown"),
        ("ready", False, "unknown"),
        ("missing", True, "unknown"),
    ],
)
def test_model_state_maps_only_recognized_consistent_public_statuses(
    status: str, installed: bool, expected: ModelState
) -> None:
    assert model_state({"status": status, "installed": installed}) == expected


def test_model_info_normalizes_mapping_and_attribute_records() -> None:
    mapping = model_info(
        {
            "engine": "kitten",
            "target_id": "kitten-v1",
            "display_name": "Kitten v1",
            "installed": True,
            "status": "ready",
            "languages": ("en-US",),
            "revision": "rev-17",
        }
    )
    attribute = model_info(
        SimpleNamespace(
            engine="piper",
            target_id="en_US-lessac-medium",
            display_name="English US",
            installed=False,
            status="missing",
            languages=("en-US", "de-DE"),
            revision=None,
        )
    )

    assert mapping == EngineModelInfo(
        engine="kitten",
        target="kitten-v1",
        language="en-US",
        label="Kitten v1",
        installed=True,
        state="ready",
        local_version="rev-17",
    )
    assert attribute.engine == "piper"
    assert attribute.target == "en_US-lessac-medium"
    assert attribute.language is None
    assert attribute.state == "missing"
    assert attribute.local_version is None


def test_model_info_does_not_invent_metadata_missing_from_voicerender() -> None:
    info = model_info(
        {
            "engine": "kitten",
            "target_id": "kitten-v1",
            "display_name": "Kitten v1",
            "installed": True,
            "status": "ready",
        }
    )

    assert info.available is False
    assert info.local_version is None
    assert info.available_version is None
    assert info.update_state == "unknown"
    assert info.bytes_installed is None
    assert info.bytes_download is None
    assert info.storage_id is None
    assert not info.installable
    assert not info.updatable
    assert not info.removable
    assert not info.verifiable
    assert model_update_state({"status": "outdated"}) == "unknown"


def test_model_info_reports_inconsistent_or_unidentified_records() -> None:
    inconsistent = model_info(
        {
            "engine": "kitten",
            "target_id": "kitten-v1",
            "installed": False,
            "status": "ready",
            "revision": "rev-17",
        }
    )
    assert inconsistent.state == "unknown"
    assert inconsistent.local_version is None
    assert inconsistent.reason is not None

    with pytest.raises(CatalogError, match="target identifier"):
        model_info({"engine": "kitten", "target_id": "", "installed": False})
    with pytest.raises(CatalogError, match="engine identifier"):
        model_info({"engine": " ", "target_id": "v1", "installed": False})


def test_engine_model_info_rejects_negative_byte_counts() -> None:
    with pytest.raises(ValueError, match="bytes_installed"):
        EngineModelInfo("kitten", "v1", bytes_installed=-1)
    with pytest.raises(ValueError, match="bytes_download"):
        EngineModelInfo("kitten", "v1", bytes_download=-1)

    assert EngineModelInfo("kitten", "v1", bytes_installed=0).bytes_installed == 0
