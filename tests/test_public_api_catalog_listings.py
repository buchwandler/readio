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
        ref="kokoro:de-fixture/fixture",
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
    assert app.catalog.normalize_engine("kokoro") == "pykokoro"
    assert app.catalog.normalize_engine("pipersynth") == "piper"
    expected_metadata = {
        "source": "fixture-cache",
        "registry_source": "fixture-cache",
        "cache_fallback": True,
        "offline": True,
        "refreshed": True,
    }

    model_listing = app.catalog.models_listing(
        ModelQuery(language="de", engine="kokoro"), discovery=options
    )
    voice_listing = app.catalog.voices_listing(
        VoiceQuery(language="de", engine="kokoro"), discovery=options
    )
    lexicon_listing = app.catalog.lexicons_listing(
        LexiconQuery(language="de", engine="pykokoro"), discovery=options
    )

    assert model_listing.items[0].id == "de-fixture"
    assert voice_listing.items[0].ref == "kokoro:de-fixture/fixture"
    assert lexicon_listing.items[0].asset_id == "de-de:gold"
    assert model_listing.discovery.to_dict() == expected_metadata
    assert voice_listing.discovery.to_dict() == expected_metadata
    assert lexicon_listing.discovery.to_dict() == expected_metadata

    voice_show = app.catalog.voice_listing(
        "kokoro:de-fixture/fixture",
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

    second_voice = replace(voice, model="other-model", ref="kokoro:other-model/fixture")
    monkeypatch.setattr(
        "readio.api.catalog.discover_voice_catalog",
        lambda **_: ((voice, second_voice), raw),
    )
    with pytest.raises(DiscoveryError) as error:
        app.catalog.voice_listing("fixture", query=VoiceQuery(engine="pykokoro"), discovery=options)
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


def test_public_voice_metadata_is_canonical_and_language_matching_is_regional(monkeypatch) -> None:
    def entry(voice_id, language, locale, label, gender):
        return VoiceCatalogEntry(
            ref=f"piper:{voice_id}",
            id=voice_id,
            gender=gender,
            language=language,
            locale=locale,
            language_label=label,
            model=voice_id,
            source="pipersynth",
            default=False,
            status="ready",
            experimental=False,
            runtime_available=True,
            engine="piper",
        )

    entries = (
        entry("american", "en-US", "en_US", "US", "FEMALE"),
        entry("generic", "en", "en", "English", "unknown"),
        entry("british", "en-GB", "en_GB", "British English", "male"),
    )
    monkeypatch.setattr(
        "readio.api.catalog.discover_voice_catalog", lambda **_: (entries, SimpleNamespace())
    )
    app = Readio(default_config())

    specific = app.catalog.voices(VoiceQuery(language="en-us", engine="piper"))
    assert [voice.id for voice in specific] == ["american", "generic"]
    assert specific[0].ref == "piper:american"
    assert (specific[0].gender, specific[0].language, specific[0].locale) == (
        "female",
        "en",
        "en-US",
    )
    assert specific[0].language_label == "en-US"
    assert (specific[1].language, specific[1].locale, specific[1].language_label) == (
        "en",
        "en",
        "English",
    )

    generic = app.catalog.voices(VoiceQuery(language="en", engine="piper"))
    assert [voice.id for voice in generic] == ["american", "generic", "british"]


def test_pocket_voice_details_are_normalized_and_filtered_by_locale(monkeypatch) -> None:
    from readio.engines.catalog import CatalogResult, SynthesisTarget

    target = SynthesisTarget(
        engine="pocket",
        id="english",
        display_name="English",
        languages=("en",),
        voices=("alba", "british"),
        metadata={
            "voice_details": [
                {
                    "id": "alba",
                    "language": "en",
                    "locale": "en",
                    "language_label": "English",
                    "gender": "female",
                },
                {
                    "id": "british",
                    "language": "en_GB",
                    "locale": "en_GB",
                    "language_label": "GB",
                },
            ]
        },
    )
    monkeypatch.setattr(
        "readio.voices.discover_targets",
        lambda **_kwargs: CatalogResult(targets=(target,)),
    )
    app = Readio(default_config())

    specific = app.catalog.voices(VoiceQuery(language="en-us", engine="pocket"))
    assert [voice.id for voice in specific] == ["alba"]
    assert (specific[0].ref, specific[0].language, specific[0].locale) == (
        "pocket:english/alba",
        "en",
        "en",
    )
    assert specific[0].gender == "female"

    generic = app.catalog.voices(VoiceQuery(language="en", engine="pocket"))
    assert [voice.id for voice in generic] == ["alba", "british"]
    assert (generic[1].language, generic[1].locale, generic[1].language_label) == (
        "en",
        "en-GB",
        "en-GB",
    )
    assert generic[1].gender == "unknown"
