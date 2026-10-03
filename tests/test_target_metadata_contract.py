"""Catalog contract tests for typed synthesis target metadata (plan section 22)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from readio.engines.catalog import CatalogRequest, SynthesisTarget, TargetVoice
from readio.engines.registry import CANONICAL_ENGINE_IDS
from readio.voices import VoiceCatalogEntry, filter_voice_catalog

CANONICAL_GENDERS = frozenset({"female", "male", "neutral", "unknown"})


def assert_target_contract(target: SynthesisTarget) -> None:
    assert target.engine in CANONICAL_ENGINE_IDS
    assert target.id
    assert target.display_name
    for language in target.languages:
        assert language == language.strip()
        assert "_" not in language
    detail_ids = [detail.id for detail in target.voice_details]
    assert len(set(detail_ids)) == len(detail_ids)
    assert set(detail_ids) <= set(target.voices)
    for detail in target.voice_details:
        assert isinstance(detail, TargetVoice)
        assert detail.gender in CANONICAL_GENDERS
        assert detail.language and "_" not in detail.language
        assert detail.locale and "_" not in detail.locale
        assert detail.language_label.strip()
    if target.default_voice is not None:
        assert target.default_voice in target.voices
    if target.sample_rate is not None:
        assert target.sample_rate > 0
    assert target.status
    assert isinstance(target.runtime_available, bool)


def test_kitten_target_metadata_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    import kittensynth

    from readio.engines.kittensynth import KittenSynthEngineAdapter

    model = kittensynth.DiscoveredModel(
        id="nano-0.8-int8",
        display_name="Kitten Nano",
        version="0.8",
        language="en",
        quality="int8",
        sample_rate=24000,
        aliases=("nano",),
        voices=(
            kittensynth.DescribedVoice(
                id="Jasper",
                gender="male",
                language="en",
                locale="en",
                language_label="English",
            ),
            kittensynth.DescribedVoice(
                id="Bella",
                gender="female",
                language="en",
                locale="en",
                language_label="English",
            ),
        ),
        default_voice="Jasper",
        source_revision="a" * 40,
        metadata={
            "name": "Kitten Nano",
            "version": "0.8",
            "language": "en",
            "quality": "int8",
            "source_revision": "a" * 40,
        },
    )
    monkeypatch.setattr(kittensynth, "discover_models", lambda **_kwargs: (model,))
    targets = KittenSynthEngineAdapter().discover(CatalogRequest(engine="kitten"))
    assert len(targets) == 1
    assert_target_contract(targets[0])
    assert targets[0].metadata["source_revision"] == "a" * 40
    assert targets[0].default_voice == "Jasper"


def test_piper_target_metadata_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    pipersynth = pytest.importorskip("pipersynth")
    from readio.engines.pipersynth import PiperSynthEngineAdapter

    class FakeManager:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def list_voices(self, **kwargs: Any) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(
                    id="thorsten",
                    name="Thorsten",
                    language_code="de-DE",
                    language_family="German",
                    language_label="DE",
                    gender="male",
                    sample_rate=22050,
                    quality="int8",
                    aliases=("thorsten-de",),
                    speaker_id_map={},
                    num_speakers=0,
                    source_revision="b" * 40,
                )
            ]

    monkeypatch.setattr(pipersynth, "VoiceAssetManager", FakeManager)
    targets = PiperSynthEngineAdapter().discover(CatalogRequest(engine="piper"))
    assert len(targets) == 1
    assert_target_contract(targets[0])
    assert targets[0].voice_details[0].gender == "male"
    assert targets[0].default_voice == "thorsten"


def test_pocket_target_metadata_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    pocketsynth = pytest.importorskip("pocketsynth")
    from readio.engines.pocketsynth import PocketSynthEngineAdapter

    bundle = SimpleNamespace(
        id="english-2026",
        aliases=("english",),
        sample_rate=24000,
        voices=("alba", "bella"),
        metadata={
            "language": "en_US",
            "predefined_voice_names": ["alba", "bella"],
            "default_voice": "alba",
            "profiles": {"fp32": {}, "int8": {}},
            "voice_details": [
                {
                    "id": "alba",
                    "language": "en",
                    "locale": "en",
                    "language_label": "English",
                    "gender": "female",
                },
                {"id": "bella", "language": "en", "locale": "en", "language_label": "English"},
            ],
        },
    )

    class FakeManager:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def list_bundles(self, **kwargs: Any) -> list[SimpleNamespace]:
            return [bundle]

    monkeypatch.setattr(pocketsynth, "BundleAssetManager", FakeManager)
    targets = PocketSynthEngineAdapter().discover(CatalogRequest(engine="pocket"))
    assert len(targets) == 1
    assert_target_contract(targets[0])
    assert targets[0].default_voice == "alba"
    assert [detail.id for detail in targets[0].voice_details] == ["alba", "bella"]


def test_kokoro_target_metadata_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    from readio.engines.pykokoro import PyKokoroEngineAdapter

    capabilities = SimpleNamespace(
        model_id="v1.0",
        engine="kokoro",
        source="github",
        languages=("de-de",),
        voices=("thorsten", "petra"),
        default_voice="thorsten",
        qualities=("fp32",),
        g2p_backend="lexphon",
        lexicons=(),
        frontend="utterplan",
        status="ready",
        experimental=False,
        runtime_available=True,
        redistribution_allowed=True,
        sample_rate=24000,
        max_tokens=512,
        provider=None,
        distribution_id=None,
        voice_details=(
            SimpleNamespace(
                name="thorsten",
                gender="male",
                language="de",
                locale="de-de",
                language_label="Deutsch",
            ),
            SimpleNamespace(
                name="petra",
                gender="female",
                language="de",
                locale="de-de",
                language_label="Deutsch",
            ),
        ),
    )
    monkeypatch.setattr(
        "readio.engines.pykokoro._pykokoro_discovery",
        lambda: lambda **_kwargs: SimpleNamespace(models=(capabilities,)),
    )
    targets = PyKokoroEngineAdapter().discover(CatalogRequest(engine="kokoro"))
    assert len(targets) == 1
    assert_target_contract(targets[0])
    assert targets[0].default_voice == "thorsten"


def _contract_entries(engine: str) -> tuple[VoiceCatalogEntry, ...]:
    def entry(voice_id: str, language: str, locale: str) -> VoiceCatalogEntry:
        return VoiceCatalogEntry(
            ref=f"{engine}:model/{voice_id}",
            id=voice_id,
            gender="unknown",
            language=language,
            locale=locale,
            language_label=language,
            model="model",
            source="fixture",
            default=False,
            status="ready",
            experimental=False,
            runtime_available=True,
            engine=engine,
        )

    return (entry("english-voice", "en", "en-US"), entry("german-voice", "de", "de-DE"))


@pytest.mark.parametrize("engine", sorted(CANONICAL_ENGINE_IDS))
@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("en", "english-voice"),
        ("en-us", "english-voice"),
        ("en_US", "english-voice"),
        ("de", "german-voice"),
        ("de-de", "german-voice"),
    ],
)
def test_cross_engine_language_matching(engine: str, requested: str, expected: str) -> None:
    entries = _contract_entries(engine)
    matches = filter_voice_catalog(entries, language=requested)
    assert [match.id for match in matches] == [expected]
