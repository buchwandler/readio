from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from readio.api import (
    DiscoveryError,
    DiscoveryOptions,
    LexiconQuery,
    ModelQuery,
    Readio,
    VoiceQuery,
    default_config,
)
from readio.lexicons import LexiconCatalogEntry
from readio.models import ModelInfo
from readio.voices import VoiceCatalogEntry


def test_catalog_listings_preserve_registry_discovery_metadata(monkeypatch) -> None:
    model = ModelInfo(
        id="de-fixture",
        source="github",
        languages=("de",),
        voices=("fixture",),
        default_voice="fixture",
        qualities=("fp32",),
        g2p_backend=None,
        lexicons=(),
        frontend="fixture",
        status="ready",
        experimental=False,
        runtime_available=True,
        redistribution_allowed=True,
    )
    voice = VoiceCatalogEntry(
        selector="de-ko-1",
        slot=1,
        id="fixture",
        gender="unknown",
        language="de",
        locale="de-DE",
        language_label="German",
        model="de-fixture",
        source="github",
        default=True,
        status="ready",
        experimental=False,
        runtime_available=True,
    )
    lexicon = LexiconCatalogEntry(
        selector="gold",
        engine="pykokoro",
        language="de",
        locale="de-DE",
        asset_id="de-de:gold",
        data_backend="lexphon",
        default=True,
        installed=True,
        models=("de-fixture",),
        model_support="known",
    )
    raw = SimpleNamespace(
        registry_source="fixture-cache",
        cache_fallback=True,
        offline=True,
        refreshed=True,
    )
    monkeypatch.setattr(
        "readio.api.catalog.discover_model_info",
        lambda **_: ((model,), raw),
    )
    monkeypatch.setattr(
        "readio.api.catalog.discover_voice_catalog",
        lambda **_: ((voice,), raw),
    )
    monkeypatch.setattr(
        "readio.api.catalog.discover_lexicon_catalog",
        lambda **_: ((lexicon,), raw),
    )

    app = Readio(default_config())
    options = DiscoveryOptions(offline=True, refresh=True, preference="github")
    expected_metadata = {
        "source": "fixture-cache",
        "registry_source": "fixture-cache",
        "cache_fallback": True,
        "offline": True,
        "refreshed": True,
    }

    model_listing = app.catalog.models_listing(
        ModelQuery(language="de", engine="pykokoro"), discovery=options
    )
    voice_listing = app.catalog.voices_listing(
        VoiceQuery(language="de", engine="pykokoro"), discovery=options
    )
    lexicon_listing = app.catalog.lexicons_listing(
        LexiconQuery(language="de", engine="pykokoro"), discovery=options
    )

    assert model_listing.items[0].id == "de-fixture"
    assert voice_listing.items[0].selector == "de-ko-1"
    assert lexicon_listing.items[0].asset_id == "de-de:gold"
    assert model_listing.discovery.to_dict() == expected_metadata
    assert voice_listing.discovery.to_dict() == expected_metadata
    assert lexicon_listing.discovery.to_dict() == expected_metadata

    voice_show = app.catalog.voice_listing(
        "DE-KO-1",
        query=VoiceQuery(language="de", engine="pykokoro"),
        discovery=options,
    )
    lexicon_show = app.catalog.lexicon_listing(
        "gold",
        query=LexiconQuery(language="de", engine="pykokoro"),
        discovery=options,
    )
    assert voice_show.items == voice_listing.items
    assert lexicon_show.items == lexicon_listing.items
    assert voice_show.discovery.to_dict() == expected_metadata
    assert lexicon_show.discovery.to_dict() == expected_metadata

    with pytest.raises(DiscoveryError) as error:
        app.catalog.voice_listing("missing", query=VoiceQuery(engine="pykokoro"), discovery=options)
    assert error.value.code == "catalog.voice_not_found"

    with pytest.raises(DiscoveryError) as error:
        app.catalog.lexicon_listing(
            "missing", query=LexiconQuery(engine="pykokoro"), discovery=options
        )
    assert error.value.code == "catalog.lexicon_not_found"

    second_voice = replace(voice, id="fixture-2", model="other-model")
    monkeypatch.setattr(
        "readio.api.catalog.discover_voice_catalog",
        lambda **_: ((voice, second_voice), raw),
    )
    with pytest.raises(DiscoveryError) as error:
        app.catalog.voice_listing("de-ko-1", query=VoiceQuery(engine="pykokoro"), discovery=options)
    assert error.value.code == "catalog.voice_ambiguous"
    assert len(error.value.details["qualified_ids"]) == 2

    second_lexicon = replace(lexicon, asset_id="de-de:other")
    monkeypatch.setattr(
        "readio.api.catalog.discover_lexicon_catalog",
        lambda **_: ((lexicon, second_lexicon), raw),
    )
    with pytest.raises(DiscoveryError) as error:
        app.catalog.lexicon_listing(
            "gold", query=LexiconQuery(engine="pykokoro"), discovery=options
        )
    assert error.value.code == "catalog.lexicon_ambiguous"
    assert len(error.value.details["asset_ids"]) == 2
