from __future__ import annotations

import pytest

from readio.engines.catalog import CatalogResult, SynthesisTarget, TargetVoice
from readio.models import ModelDiscoveryError
from readio.voices import resolve_voice_reference


def target(
    engine: str,
    target_id: str,
    voices: tuple[str, ...],
    *,
    language: str = "en",
    locale: str = "en-US",
) -> SynthesisTarget:
    return SynthesisTarget(
        engine=engine,
        id=target_id,
        display_name=target_id,
        languages=(locale,),
        voices=voices,
        metadata={
            "default_voice": voices[0] if voices else None,
            "voice_details": [
                {
                    "id": voice,
                    "gender": "unknown",
                    "language": language,
                    "locale": locale,
                    "language_label": locale,
                }
                for voice in voices
            ],
        },
    )


def discover(monkeypatch, targets: tuple[SynthesisTarget, ...]) -> None:
    monkeypatch.setattr(
        "readio.voices.discover_targets",
        lambda **kwargs: CatalogResult(targets=targets),
    )


def test_semantic_reference_resolves_each_engine_to_structured_identity(monkeypatch) -> None:
    discover(
        monkeypatch,
        (
            target("pykokoro", "v1.0", ("af_heart",)),
            target("piper", "en_US-amy-medium", ("en_US-amy-medium",)),
            target("pocket", "english_2026-04", ("alba",)),
        ),
    )

    expected = (
        ("kokoro:v1.0/af_heart", "pykokoro", "v1.0", "af_heart"),
        ("piper:en_US-amy-medium", "piper", "en_US-amy-medium", "en_US-amy-medium"),
        ("pocket:english_2026-04/alba", "pocket", "english_2026-04", "alba"),
    )
    for ref, engine, target_id, voice in expected:
        resolved = resolve_voice_reference(
            ref,
            language=None,
            model=None,
            source=None,
        )
        assert resolved is not None
        assert (resolved.ref, resolved.engine, resolved.target_id, resolved.voice) == (
            ref,
            engine,
            target_id,
            voice,
        )


def test_semantic_reference_rejects_engine_and_target_conflicts(monkeypatch) -> None:
    discover(monkeypatch, (target("pykokoro", "v1.0", ("af_heart",)),))

    with pytest.raises(ModelDiscoveryError, match="engine.*requested"):
        resolve_voice_reference(
            "kokoro:v1.0/af_heart",
            language=None,
            model=None,
            source=None,
            engine="piper",
        )
    with pytest.raises(ModelDiscoveryError, match="targets.*requested"):
        resolve_voice_reference(
            "kokoro:v1.0/af_heart",
            language=None,
            model="v1.1-zh",
            source=None,
        )


def test_native_voice_id_requires_unique_context(monkeypatch) -> None:
    discover(
        monkeypatch,
        (
            target("pykokoro", "v1.0", ("af_heart",)),
            target("pocket", "bundle", ("af_heart",)),
        ),
    )

    with pytest.raises(ModelDiscoveryError, match="ambiguous"):
        resolve_voice_reference("af_heart", language=None, model=None, source=None)

    resolved = resolve_voice_reference(
        "af_heart",
        language=None,
        model="v1.0",
        source=None,
        engine="kokoro",
    )
    assert resolved is not None
    assert resolved.ref == "kokoro:v1.0/af_heart"


def test_unqualified_multi_voice_target_is_ambiguous(monkeypatch) -> None:
    discover(monkeypatch, (target("pykokoro", "v1.0", ("af_heart", "af_sarah")),))

    with pytest.raises(ModelDiscoveryError, match="ambiguous"):
        resolve_voice_reference(
            "kokoro:v1.0",
            language=None,
            model=None,
            source=None,
        )


def test_old_numbered_selector_is_not_resolved(monkeypatch) -> None:
    discover(monkeypatch, (target("pykokoro", "v1.0", ("af_heart",)),))

    with pytest.raises(ModelDiscoveryError, match="Native voice ID"):
        resolve_voice_reference(
            "en_us-ko-4",
            language=None,
            model=None,
            source=None,
        )


def test_language_conflict_uses_catalog_metadata(monkeypatch) -> None:
    discover(
        monkeypatch,
        (target("pykokoro", "v1.0", ("af_heart",), language="en", locale="en-US"),),
    )

    with pytest.raises(ModelDiscoveryError, match="requested language"):
        resolve_voice_reference(
            "kokoro:v1.0/af_heart",
            language="en-GB",
            model=None,
            source=None,
        )


def test_piper_semantic_reference_keeps_the_public_british_locale(monkeypatch) -> None:
    discover(
        monkeypatch,
        (
            target(
                "piper",
                "en_GB-alba-medium",
                ("en_GB-alba-medium",),
                language="en",
                locale="en-gb",
            ),
        ),
    )

    resolved = resolve_voice_reference(
        "piper:en_GB-alba-medium",
        language=None,
        model=None,
        source=None,
    )

    assert resolved is not None
    assert resolved.ref == "piper:en_GB-alba-medium"
    assert resolved.language == "en-gb"
    assert resolved.target_id == "en_GB-alba-medium"
    assert resolved.engine == "piper"
    assert resolved.voice == "en_GB-alba-medium"
    assert resolved.catalog_entry.locale == "en-gb"
    assert "en-gb-x-rp" not in str(resolved.catalog_entry.to_dict())


def test_pocket_base_language_capability_accepts_regional_request_and_preserves_it(
    monkeypatch,
) -> None:
    discover(
        monkeypatch,
        (
            SynthesisTarget(
                engine="pocket",
                id="english_2026-04",
                display_name="English Pocket",
                languages=("en",),
                voices=("alba",),
                voice_details=(
                    TargetVoice(
                        id="alba",
                        gender="unknown",
                        language="en",
                        locale="en",
                        language_label="English",
                        languages=("en",),
                    ),
                ),
            ),
        ),
    )

    resolved = resolve_voice_reference(
        "pocket:english_2026-04/alba",
        language="en-US",
        model=None,
        source=None,
    )

    assert resolved is not None
    assert resolved.language == "en-us"
    assert resolved.catalog_entry.locale == "en"
    assert resolved.catalog_entry.languages == ("en",)
