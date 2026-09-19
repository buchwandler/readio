from __future__ import annotations

from types import SimpleNamespace

from readio.models import ModelInfo, VoiceMetadata
from readio.voices import build_voice_catalog, filter_voice_catalog, resolve_voice_selector


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


def test_catalog_orders_models_and_numbers_each_locale() -> None:
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
        ("en-us-1", "af_a"),
        ("en-us-2", "af_b"),
        ("en-gb-1", "bf_a"),
        ("de-1", "anna"),
        ("de-2", "martin"),
    ]


def test_filtering_does_not_renumber_and_language_is_regional() -> None:
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
    assert [entry.selector for entry in filter_voice_catalog(entries, gender="male")] == ["de-2"]
    assert {entry.locale for entry in filter_voice_catalog(entries, language="en")} == {
        "en-US",
        "en-GB",
    }
    assert {entry.locale for entry in filter_voice_catalog(entries, language="en-us")} == {"en-US"}


def test_missing_voice_metadata_uses_conservative_fallback() -> None:
    catalog = build_voice_catalog((model("fallback", ("voice",), ()),))
    entry = catalog.voices[0]
    assert entry.gender == "unknown"
    assert entry.language == "en"
    assert entry.locale == "en"


def test_selector_resolution_expands_canonical_identity(monkeypatch) -> None:
    entry = build_voice_catalog(
        (
            model(
                "de-model",
                ("martin",),
                (VoiceMetadata("martin", "male", "de", "de", "German"),),
            ),
        )
    ).voices[0]
    monkeypatch.setattr(
        "readio.voices.discover_voice_catalog",
        lambda **_: ((entry,), SimpleNamespace()),
    )
    resolved = resolve_voice_selector("de-1", language=None, model=None, source=None)
    assert resolved is not None
    assert (resolved.language, resolved.model, resolved.source, resolved.voice) == (
        "de",
        "de-model",
        "github",
        "martin",
    )


def test_piper_discovery_projects_voice_bundle_targets(monkeypatch) -> None:
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
        engine="pipersynth",
        language="de",
        offline=True,
        refresh=True,
    )
    assert result.offline is True
    assert result.refreshed is True
    assert entries[0].engine == "piper"
    assert entries[0].selector == "de_DE-thorsten-medium"
    assert entries[0].id == "de_DE-thorsten-medium"
