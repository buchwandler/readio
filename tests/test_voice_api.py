from __future__ import annotations

from types import SimpleNamespace

import pytest

from readio.api import DiscoveryError, Readio, VoiceQuery, default_config
from readio.voices import VoiceCatalogEntry


def catalog_entries() -> tuple[VoiceCatalogEntry, ...]:
    common = {
        "gender": "unknown",
        "language": "en",
        "locale": "en-US",
        "language_label": "American English",
        "source": "fixture",
        "default": True,
        "status": "ready",
        "experimental": False,
        "runtime_available": True,
    }
    return (
        VoiceCatalogEntry(
            ref="kokoro:v1.0/af_heart",
            id="af_heart",
            model="v1.0",
            engine="pykokoro",
            **common,
        ),
        VoiceCatalogEntry(
            ref="piper:en_US-amy-medium",
            id="en_US-amy-medium",
            model="en_US-amy-medium",
            engine="piper",
            **common,
        ),
        VoiceCatalogEntry(
            ref="pocket:english_2026-04/alba",
            id="alba",
            model="english_2026-04",
            engine="pocket",
            **common,
        ),
    )


def target(engine: str, target_id: str, voice: str):
    from readio.engines.catalog import SynthesisTarget

    return SynthesisTarget(
        engine=engine,
        id=target_id,
        display_name=target_id,
        languages=("en-US",),
        voices=(voice,),
        metadata={
            "default_voice": voice,
            "source": "fixture",
            "voice_details": [
                {
                    "id": voice,
                    "gender": "unknown",
                    "language": "en",
                    "locale": "en-US",
                    "language_label": "American English",
                }
            ],
        },
    )


def test_public_catalog_listing_and_reference_lookup_all_engines(monkeypatch) -> None:
    raw = SimpleNamespace(
        registry_source="fixture",
        cache_fallback=False,
        offline=False,
        refreshed=False,
    )
    monkeypatch.setattr(
        "readio.api.catalog.discover_voice_catalog",
        lambda **kwargs: (catalog_entries(), raw),
    )
    app = Readio(default_config())

    listing = app.catalog.voices_listing()
    assert [(item.ref, item.engine, item.target_id) for item in listing.items] == [
        ("kokoro:v1.0/af_heart", "kokoro", "v1.0"),
        ("piper:en_US-amy-medium", "piper", "en_US-amy-medium"),
        ("pocket:english_2026-04/alba", "pocket", "english_2026-04"),
    ]
    assert all("selector" not in item.to_dict() for item in listing.items)
    assert app.catalog.voice_listing("pocket:english_2026-04/alba").items[0] == listing.items[2]


def test_catalog_lookup_reports_explicit_reference_conflicts(monkeypatch) -> None:
    monkeypatch.setattr(
        "readio.api.catalog.discover_voice_catalog",
        lambda **kwargs: (catalog_entries(), SimpleNamespace()),
    )
    app = Readio(default_config())

    with pytest.raises(DiscoveryError) as engine_error:
        app.catalog.voice_listing(
            "kokoro:v1.0/af_heart",
            query=VoiceQuery(engine="piper"),
        )
    assert engine_error.value.code == "catalog.voice_reference_engine_conflict"

    with pytest.raises(DiscoveryError) as target_error:
        app.catalog.voice_listing(
            "kokoro:v1.0/af_heart",
            query=VoiceQuery(model="v1.1-zh"),
        )
    assert target_error.value.code == "catalog.voice_reference_target_conflict"


def test_public_voice_resolution_infers_engine_and_target(monkeypatch) -> None:
    from readio.engines.catalog import CatalogResult

    monkeypatch.setattr(
        "readio.voices.discover_targets",
        lambda **kwargs: CatalogResult(
            targets=(
                target("pykokoro", "v1.0", "af_heart"),
                target("piper", "en_US-amy-medium", "en_US-amy-medium"),
                target("pocket", "english_2026-04", "alba"),
            )
        ),
    )
    resolution = Readio(default_config()).catalog.resolve_voice("pocket:english_2026-04/alba")

    assert (resolution.ref, resolution.engine, resolution.target_id, resolution.voice) == (
        "pocket:english_2026-04/alba",
        "pocket",
        "english_2026-04",
        "alba",
    )
    assert resolution.catalog_entry is not None
    assert resolution.catalog_entry.engine == "pocket"


def test_cli_voice_details_show_reference_without_ordinal_fields(capsys) -> None:
    from readio.api.types import VoiceInfo
    from readio.cli import _voice_cli_dict, _voice_entry_human

    entry = VoiceInfo(
        ref="kokoro:v1.0/af_heart",
        id="af_heart",
        gender="female",
        language="en",
        locale="en-US",
        language_label="American English",
        model="v1.0",
        source="fixture",
        default=True,
        status="ready",
        experimental=False,
        runtime_available=True,
        engine="kokoro",
    )

    payload = _voice_cli_dict(entry)
    _voice_entry_human(entry)
    output = capsys.readouterr().out
    assert payload["ref"] == "kokoro:v1.0/af_heart"
    assert "number" not in payload
    assert "selector_status" not in payload
    assert "Voice ref:      kokoro:v1.0/af_heart" in output
    assert "Selector:" not in output
    assert "Slot:" not in output


def test_cli_voice_list_table_uses_voice_refs(capsys, monkeypatch) -> None:
    from argparse import Namespace

    from readio import cli
    from readio.api.types import CatalogDiscovery, CatalogListing, VoiceInfo

    entry = VoiceInfo(
        ref="pocket:english_2026-04/alba",
        id="alba",
        gender="female",
        language="en",
        locale="en",
        language_label="English",
        model="english_2026-04",
        source="fixture",
        default=True,
        status="ready",
        experimental=False,
        runtime_available=True,
        engine="pocket",
    )
    listing_discovery = CatalogDiscovery(
        registry_source="engine-adapters",
        cache_fallback=False,
        offline=False,
        refreshed=False,
    )
    catalog = SimpleNamespace(
        engines=lambda: (SimpleNamespace(id="pocket"),),
        normalize_engine=lambda engine: engine,
        voices_listing=lambda _query, **_kwargs: CatalogListing((entry,), listing_discovery),
    )
    monkeypatch.setattr(cli, "_api_for", lambda _args: SimpleNamespace(catalog=catalog))
    args = Namespace(
        lang=None,
        language=None,
        offline=False,
        refresh=False,
        preference="auto",
        voices_command="list",
        engine=None,
        model=None,
        gender=None,
        json=False,
    )

    assert cli._cmd_voices(args) == 0
    output = capsys.readouterr().out
    assert "VOICE REF" in output
    assert "pocket:english_2026-04/alba" in output
    assert "SELECTOR" not in output
    assert "slot" not in output.lower()
