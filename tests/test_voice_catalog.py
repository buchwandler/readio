from __future__ import annotations

from readio.models import ModelInfo, VoiceMetadata
from readio.voices import VoiceCatalogEntry, build_voice_catalog, filter_voice_catalog


def model(
    model_id: str,
    voices: tuple[str, ...],
    details: tuple[VoiceMetadata, ...],
    *,
    engine: str = "kokoro",
    source: str = "github",
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
        status="ready",
        experimental=False,
        runtime_available=True,
        redistribution_allowed=True,
        voice_details=details,
        engine=engine,
    )


def test_catalog_builds_semantic_refs_for_all_voice_shapes() -> None:
    catalog = build_voice_catalog(
        (
            model(
                "v1.0",
                ("af_heart",),
                (VoiceMetadata("af_heart", "female", "en", "en-US", "American English"),),
            ),
            model("en_US-amy-medium", ("en_US-amy-medium",), (), engine="piper"),
            model(
                "english_2026-04",
                ("alba",),
                (VoiceMetadata("alba", "female", "en", "en", "English"),),
                engine="pocket",
            ),
        )
    )

    assert [entry.ref for entry in catalog.voices] == [
        "kokoro:v1.0/af_heart",
        "piper:en_US-amy-medium",
        "pocket:english_2026-04/alba",
    ]
    assert [entry.target_id for entry in catalog.voices] == [
        "v1.0",
        "en_US-amy-medium",
        "english_2026-04",
    ]
    assert "backend" not in catalog.voices[0].to_dict()
    assert not hasattr(catalog.voices[0], "backend")
    assert "engine" in model("model", ("voice",), ()).to_dict()
    assert "backend" not in model("model", ("voice",), ()).to_dict()
    assert all(entry.to_dict()["ref"] for entry in catalog.voices)
    assert all("selector" not in entry.to_dict() for entry in catalog.voices)


def test_catalog_reference_does_not_depend_on_order_or_language_metadata() -> None:
    first = build_voice_catalog(
        (
            model(
                "model-a",
                ("voice-a",),
                (VoiceMetadata("voice-a", "female", "en", "en-US", "American English"),),
            ),
            model(
                "model-b",
                ("voice-b",),
                (VoiceMetadata("voice-b", "female", "en", "en-GB", "British English"),),
            ),
        )
    ).voices
    reordered = build_voice_catalog(
        (
            model(
                "model-b",
                ("voice-b",),
                (VoiceMetadata("voice-b", "female", "en", "en", "English"),),
            ),
            model(
                "model-a",
                ("voice-a",),
                (VoiceMetadata("voice-a", "female", "en", "en", "English"),),
            ),
        )
    ).voices

    refs_by_id = {entry.id: entry.ref for entry in first}
    assert {entry.id: entry.ref for entry in reordered} == refs_by_id
    assert refs_by_id == {
        "voice-a": "kokoro:model-a/voice-a",
        "voice-b": "kokoro:model-b/voice-b",
    }


def test_catalog_filters_use_voice_metadata_without_changing_refs() -> None:
    entries = build_voice_catalog(
        (
            model(
                "kokoro-v1",
                ("american", "british", "unassigned"),
                (
                    VoiceMetadata("american", "female", "en", "en-US", "American English"),
                    VoiceMetadata("british", "male", "en", "en-GB", "British English"),
                ),
            ),
        )
    ).voices

    assert {entry.ref for entry in filter_voice_catalog(entries, language="en-us")} == {
        "kokoro:kokoro-v1/american"
    }
    assert {entry.ref for entry in filter_voice_catalog(entries, gender="male")} == {
        "kokoro:kokoro-v1/british"
    }
    unassigned = next(entry for entry in entries if entry.id == "unassigned")
    assert unassigned.ref == "kokoro:kokoro-v1/unassigned"
    assert unassigned.locale == "en"


def test_piper_discovery_preserves_target_identity_and_language_metadata(monkeypatch) -> None:
    import readio.voices as voices_module
    from readio.engines.catalog import CatalogResult, SynthesisTarget

    target = SynthesisTarget(
        engine="piper",
        id="en_US-amy-medium",
        display_name="Amy",
        languages=("en-us",),
        metadata={
            "language": "en",
            "locale": "en-us",
            "language_label": "American English",
            "gender": "female",
        },
    )
    monkeypatch.setattr(
        voices_module,
        "discover_targets",
        lambda **kwargs: CatalogResult(targets=(target,)),
    )

    entries, _ = voices_module.discover_voice_catalog(engine="pipersynth", language="en-us")
    entry = entries[0]
    assert entry.ref == "piper:en_US-amy-medium"
    assert entry.target_id == "en_US-amy-medium"
    assert entry.language == "en"
    assert entry.locale == "en-us"
    assert entry.language_label == "American English"
    assert entry.gender == "female"


def test_multilingual_capabilities_match_base_and_specific_language_tags() -> None:
    entry = VoiceCatalogEntry(
        ref="supertonic:supertonic-3/F1",
        id="F1",
        gender="unknown",
        language="en",
        locale="en",
        language_label="Multilingual",
        model="supertonic-3",
        source="supertonicsynth",
        default=True,
        status="ready",
        experimental=False,
        runtime_available=True,
        engine="supertonic",
        languages=("en", "de", "ja"),
    )

    for requested in ("en", "en-US", "de", "de-DE", "ja"):
        assert filter_voice_catalog((entry,), language=requested) == (entry,)
    assert filter_voice_catalog((entry,), language="fr") == ()


def test_multilingual_capabilities_do_not_match_distinct_specific_locales() -> None:
    entry = VoiceCatalogEntry(
        ref="supertonic:target/F1",
        id="F1",
        gender="unknown",
        language="en",
        locale="en-US",
        language_label="Multilingual",
        model="target",
        source="supertonicsynth",
        default=True,
        status="ready",
        experimental=False,
        runtime_available=True,
        languages=("en-GB",),
    )

    assert filter_voice_catalog((entry,), language="en-US") == ()
