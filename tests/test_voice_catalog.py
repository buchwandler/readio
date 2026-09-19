from __future__ import annotations

from types import SimpleNamespace

from readio.models import ModelDiscoveryError, ModelInfo, VoiceMetadata
from readio.voices import (
    VoiceCatalogEntry,
    build_voice_catalog,
    filter_voice_catalog,
    resolve_voice_selector,
)


def model(
    model_id: str,
    voices: tuple[str, ...],
    details: tuple[VoiceMetadata, ...],
    *,
    source: str = "github",
    priority_status: str = "ready",
) -> ModelInfo:
    return ModelInfo(
        id=model_id,
        source=source,
        languages=("en",),
        voices=voices,
        default_voice=voices[0],
        qualities=("fp32",),
        g2p_backend="kokorog2p",
        lexicons=(),
        frontend="frontend",
        status=priority_status,
        experimental=priority_status == "experimental",
        runtime_available=True,
        redistribution_allowed=True,
        voice_details=details,
    )


def identity(selector: str, language: str, slot: int, asset_id: str, voice_id: str):
    return SimpleNamespace(
        selector=selector,
        language=language,
        engine_code="ko",
        slot=slot,
        system="kokoro",
        asset_id=asset_id,
        voice_id=voice_id,
    )


def test_catalog_projects_authoritative_identities_and_display_orders(monkeypatch) -> None:
    identities = {
        ("v1.0", "af_a"): identity("en_us-ko-1", "en_us", 1, "v1.0", "af_a"),
        ("v1.0", "af_b"): identity("en_us-ko-2", "en_us", 2, "v1.0", "af_b"),
        ("v1.0", "bf_a"): identity("en_gb-ko-1", "en_gb", 1, "v1.0", "bf_a"),
        ("de-model", "anna"): identity("de-ko-1", "de", 1, "de-model", "anna"),
        ("de-model", "martin"): identity("de-ko-2", "de", 2, "de-model", "martin"),
    }
    monkeypatch.setattr(
        "readio.voices.selector_for_voice",
        lambda *, system, asset_id, voice_id: identities.get((asset_id, voice_id)),
    )
    catalog = build_voice_catalog(
        (
            model(
                "de-model",
                ("anna", "martin"),
                (
                    VoiceMetadata("anna", "female", "de", "de", "German"),
                    VoiceMetadata("martin", "male", "de", "de", "German"),
                ),
            ),
            model(
                "v1.0",
                ("af_a", "af_b", "bf_a"),
                (
                    VoiceMetadata("af_a", "female", "en", "en-US", "American English"),
                    VoiceMetadata("af_b", "female", "en", "en-US", "American English"),
                    VoiceMetadata("bf_a", "female", "en", "en-GB", "British English"),
                ),
            ),
        )
    )
    assert [(entry.selector, entry.id) for entry in catalog.voices] == [
        ("en_us-ko-1", "af_a"),
        ("en_us-ko-2", "af_b"),
        ("en_gb-ko-1", "bf_a"),
        ("de-ko-1", "anna"),
        ("de-ko-2", "martin"),
    ]
    assert catalog.voices[-1].slot == 2
    assert catalog.voices[-1].number == 2


def test_filtering_does_not_renumber_and_language_is_regional(monkeypatch) -> None:
    monkeypatch.setattr(
        "readio.voices.selector_for_voice",
        lambda *, system, asset_id, voice_id: (
            identity("de-ko-1", "de", 1, asset_id, voice_id)
            if asset_id == "de-model" and voice_id == "anna"
            else identity("de-ko-2", "de", 2, asset_id, voice_id)
            if asset_id == "de-model" and voice_id == "martin"
            else None
        ),
    )
    entries = build_voice_catalog(
        (
            model(
                "de-model",
                ("anna", "martin", "petra"),
                (
                    VoiceMetadata("anna", "female", "de", "de", "German"),
                    VoiceMetadata("martin", "male", "de", "de", "German"),
                    VoiceMetadata("petra", "female", "de", "de", "German"),
                ),
            ),
            model(
                "en-model",
                ("us", "gb"),
                (
                    VoiceMetadata("us", "female", "en", "en-US", "American English"),
                    VoiceMetadata("gb", "female", "en", "en-GB", "British English"),
                ),
            ),
        )
    ).voices
    assert [entry.selector for entry in filter_voice_catalog(entries, gender="male")] == ["de-ko-2"]
    assert [
        entry.selector
        for entry in filter_voice_catalog(entries, gender="female")
        if entry.id == "petra"
    ] == [None]
    assert {entry.locale for entry in filter_voice_catalog(entries, language="en")} == {
        "en-US",
        "en-GB",
    }
    assert {entry.locale for entry in filter_voice_catalog(entries, language="en-us")} == {"en-US"}


def test_unassigned_voice_never_receives_local_slot(monkeypatch) -> None:
    monkeypatch.setattr("readio.voices.selector_for_voice", lambda **_: None)
    entry = build_voice_catalog((model("future", ("voice",), ()),)).voices[0]
    assert entry.selector is None
    assert entry.slot is None
    assert entry.to_dict()["selector_status"] == "unassigned"


def test_selector_resolution_expands_canonical_identity(monkeypatch) -> None:
    entry = VoiceCatalogEntry(
        selector="de-ko-3",
        slot=3,
        selector_language="de",
        selector_engine_code="ko",
        id="thorsten",
        gender="male",
        language="de",
        locale="de",
        language_label="German",
        model="de-thorsten",
        source="github",
        default=True,
        status="ready",
        experimental=False,
        runtime_available=True,
        engine="pykokoro",
    )
    monkeypatch.setattr(
        "readio.voices.discover_voice_catalog",
        lambda **_: ((entry,), SimpleNamespace()),
    )
    resolved = resolve_voice_selector("de-ko-3", language=None, model=None, source=None)
    assert resolved is not None
    assert (
        resolved.selector,
        resolved.language,
        resolved.model,
        resolved.source,
        resolved.voice,
    ) == (
        "de-ko-3",
        "de",
        "de-thorsten",
        "github",
        "thorsten",
    )


def test_legacy_selector_is_canonicalized_with_warning(monkeypatch) -> None:
    entry = VoiceCatalogEntry(
        selector="de-ko-3",
        slot=3,
        id="thorsten",
        gender="male",
        language="de",
        locale="de",
        language_label="German",
        model="de-thorsten",
        source="github",
        default=True,
        status="ready",
        experimental=False,
        runtime_available=True,
        engine="pykokoro",
    )
    monkeypatch.setattr(
        "readio.voices.discover_voice_catalog", lambda **_: ((entry,), SimpleNamespace())
    )
    import pytest

    with pytest.warns(UserWarning, match="de-ko-3"):
        resolved = resolve_voice_selector("de-3", language=None, model=None, source=None)
    assert resolved is not None
    assert resolved.selector == "de-ko-3"


def test_cross_engine_selector_conflict_is_explicit() -> None:
    import pytest

    with pytest.raises(ModelDiscoveryError, match="engine.*requested") as error:
        resolve_voice_selector("de-pi-9", language=None, model=None, source=None, engine="kokoro")
    assert "de-pi-9" in str(error.value)


def test_piper_discovery_projects_authoritative_selector(monkeypatch) -> None:
    import readio.voices as voices_module
    from readio.engines.catalog import CatalogResult, SynthesisTarget

    target = SynthesisTarget(
        engine="piper",
        id="de_DE-thorsten-medium",
        display_name="Thorsten",
        languages=("de-DE",),
        qualities=("medium",),
        metadata={"language_family": "de", "region": "DE"},
    )
    monkeypatch.setattr(
        voices_module,
        "discover_targets",
        lambda **kwargs: CatalogResult(
            targets=(target,),
            registry_source="engine-adapters",
            offline=kwargs["offline"],
            refreshed=kwargs["refresh"],
        ),
    )

    entries, result = voices_module.discover_voice_catalog(
        engine="pipersynth", language="de", offline=True, refresh=True
    )
    assert result.offline is True
    assert result.refreshed is True
    assert entries[0].engine == "piper"
    assert entries[0].selector == "de-pi-9"
    assert entries[0].slot == 9
    assert entries[0].id == "de_DE-thorsten-medium"
