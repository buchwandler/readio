from __future__ import annotations

import importlib.metadata
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import numpy as np
import pytest

from readio.engines.base import EngineSelection, RenderedSpeech, SpeechRequest
from readio.engines.catalog import CatalogRequest
from readio.engines.kittensynth import (
    DEFAULT_KITTEN_MODEL,
    DEFAULT_KITTEN_VOICE,
    KittenSynthEngineAdapter,
    KittenSynthEngineSession,
)
from readio.engines.selection import EngineRequest


def _discovered_model() -> Any:
    import kittensynth

    return kittensynth.DiscoveredModel(
        id=DEFAULT_KITTEN_MODEL,
        display_name="Kitten Nano",
        version="0.8",
        language="en",
        quality="int8",
        sample_rate=24000,
        aliases=("nano-current",),
        voices=(
            kittensynth.DescribedVoice(
                id="Jasper",
                gender="unknown",
                language="en",
                locale="en",
                language_label="English",
                languages=("en",),
            ),
            kittensynth.DescribedVoice(
                id="Bella",
                gender="unknown",
                language="en",
                locale="en",
                language_label="English",
                languages=("en",),
            ),
        ),
        default_voice=DEFAULT_KITTEN_VOICE,
        source_revision="a" * 40,
        metadata={
            "name": "Kitten Nano",
            "version": "0.8",
            "language": "en",
            "quality": "int8",
            "runtime": {"profile": "ONNX2"},
            "source_revision": "a" * 40,
            "source_repository": "example/kitten",
            "license": "Apache-2.0",
            "voice_aliases": {"Jasper": "jasper", "Bella": "bella"},
        },
    )


def _install_fake_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    import kittensynth

    monkeypatch.setattr(kittensynth, "discover_models", lambda **_kwargs: (_discovered_model(),))


def test_kitten_adapter_compatible_public_api_without_loading_model() -> None:
    adapter = KittenSynthEngineAdapter()
    assert adapter.compatible_api()
    assert "KittenVoice" in vars(__import__("kittensynth"))
    assert callable(__import__("kittensynth").KittenVoice.synthesize_prepared)


def test_kitten_capabilities_match_supported_contract() -> None:
    capabilities = KittenSynthEngineAdapter().capabilities()
    assert capabilities.id == "kitten"
    assert capabilities.voice_binding_namespace == "kitten"
    assert capabilities.voice_binding_scope == "request"
    assert capabilities.supports_named_voices
    assert capabilities.supports_qualities
    assert capabilities.supports_live
    assert not capabilities.supports_reference_voice
    assert not capabilities.supports_speakers
    assert not capabilities.supports_pronunciation_overrides
    assert not capabilities.supports_linguistic_tokens
    assert not capabilities.supports_timestamps


def test_kitten_discovery_maps_public_kittensynth_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_catalog(monkeypatch)
    targets = KittenSynthEngineAdapter().discover(
        CatalogRequest(engine="kitten", language="en-us", offline=True, refresh=True)
    )
    assert len(targets) == 1
    target = targets[0]
    assert target.engine == "kitten"
    assert target.id == DEFAULT_KITTEN_MODEL
    assert target.languages == ("en",)
    assert target.sample_rate == 24000
    assert target.voices == ("Jasper", "Bella")
    assert target.qualities == ("int8",)
    assert target.metadata["source_revision"] == "a" * 40
    assert target.default_voice == DEFAULT_KITTEN_VOICE
    assert target.voice_details[0].gender == "unknown"


def test_kitten_resolve_uses_explicit_default_model_and_voice() -> None:
    selection, diagnostics = KittenSynthEngineAdapter().resolve(
        EngineRequest(engine="kitten", language="en-us")
    )
    assert diagnostics == ()
    assert selection.engine == "kitten"
    assert selection.target_id == DEFAULT_KITTEN_MODEL
    assert selection.voice == DEFAULT_KITTEN_VOICE
    assert selection.options["speed"] == 1.0


def test_kitten_resolve_rejects_non_english_language() -> None:
    with pytest.raises(ValueError, match="supports English only") as error:
        KittenSynthEngineAdapter().resolve(EngineRequest(engine="kitten", language="fr-fr"))
    assert error.value.diagnostic_code == "kitten.language_incompatible"


def test_kitten_resolve_validates_voice_and_quality(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_catalog(monkeypatch)
    adapter = KittenSynthEngineAdapter()
    selection, _ = adapter.resolve(
        EngineRequest(engine="kitten", language="en-us", voice="not-a-voice")
    )
    diagnostics = adapter.validate_selection(selection)
    assert [item.code for item in diagnostics] == ["kitten.voice_unavailable"]

    quality_selection, _ = adapter.resolve(
        EngineRequest(
            engine="kitten",
            language="en-us",
            voice="Jasper",
            options={"quality": "fp32"},
        )
    )
    diagnostics = adapter.validate_selection(quality_selection)
    assert [item.code for item in diagnostics] == ["kitten.quality_unavailable"]


def test_kitten_session_passes_prepared_text_and_maps_result_without_timings() -> None:
    class Voice:
        def synthesize_prepared(self, text: str, *, voice: str, speed: float, config=None):
            self.arguments = (text, voice, speed, config)
            return SimpleNamespace(
                audio=np.array([0.1, -0.1], dtype=np.float32),
                sample_rate=24000,
                voice=voice,
                model_ref="catalog:nano-0.8-int8",
                speed=0.9,
                metadata={"phonemes": "həˈloʊ", "token_count": 2},
            )

    voice = Voice()
    selection = EngineSelection(
        engine="kitten",
        target_id=DEFAULT_KITTEN_MODEL,
        language="en-us",
        voice="Jasper",
        options={"speed": 1.1},
    )
    request = SpeechRequest(id="chunk-1", text="Hello, (prepared)!", language="en-us")
    result = KittenSynthEngineSession(voice, selection).synthesize(request)
    assert voice.arguments == ("Hello, (prepared)!", "Jasper", 1.1, None)
    assert result.id == "chunk-1"
    assert result.word_timings == ()
    assert result.metadata["phonemes"] == "həˈloʊ"
    assert result.metadata["model_ref"] == "catalog:nano-0.8-int8"
    assert result.metadata["effective_speed"] == 0.9


def test_kitten_result_never_fabricates_word_timings() -> None:
    class Voice:
        def synthesize_prepared(self, *_args, **_kwargs):
            return SimpleNamespace(
                audio=np.ones(2, dtype=np.float32),
                sample_rate=24000,
                voice="Jasper",
                model_ref="model",
                speed=1.0,
                metadata={},
            )

    result = KittenSynthEngineSession(
        Voice(), EngineSelection("kitten", DEFAULT_KITTEN_MODEL, "en-us", voice="Jasper")
    ).synthesize(SpeechRequest("chunk", "Hello", "en-us"))
    assert isinstance(result, RenderedSpeech)
    assert result.word_timings == ()


def test_kitten_open_uses_public_api_and_closes_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    class Voice:
        def close(self) -> None:
            calls["closed"] = True

    class KittenVoice:
        @classmethod
        def from_pretrained(cls, model: str, **options: object) -> Voice:
            calls["model"] = model
            calls["options"] = options
            return Voice()

    module = ModuleType("kittensynth")
    module.KittenVoice = KittenVoice
    monkeypatch.setitem(sys.modules, "kittensynth", module)
    selection = EngineSelection(
        engine="kitten",
        target_id=DEFAULT_KITTEN_MODEL,
        language="en-us",
        voice="Jasper",
        options={"quality": "int8", "cache_dir": "/tmp/models"},
        offline=True,
        refresh=True,
    )
    with KittenSynthEngineAdapter().open(selection) as session:
        assert isinstance(session, KittenSynthEngineSession)
    assert calls["model"] == DEFAULT_KITTEN_MODEL
    assert calls["options"]["quality"] == "int8"
    assert calls["options"]["offline"] is True
    assert calls["options"]["refresh_catalog"] is True
    assert calls["closed"] is True


def test_kitten_error_translation_has_stable_diagnostic_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InvalidVoiceError(Exception):
        pass

    module = ModuleType("kittensynth")
    module.InvalidVoiceError = InvalidVoiceError
    monkeypatch.setitem(sys.modules, "kittensynth", module)
    selection = EngineSelection("kitten", DEFAULT_KITTEN_MODEL, "en-us", voice="missing")
    request = SpeechRequest("chunk", "Hello", "en-us", voice="missing")
    from readio.engines.kittensynth import _translate_kitten_error

    error = _translate_kitten_error(InvalidVoiceError("no voice"), selection, request)
    assert error.code == "kitten.voice_unavailable"
    assert error.native_error_type == "InvalidVoiceError"


def test_kitten_cache_identity_uses_engine_owned_runtime_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_identity = {
        "engine_version": "0.1.0",
        "runtime_revision": "onnxvoice-0.2.0",
        "g2p_revision": "0.1.1",
        "catalog_revision": None,
        "model_revision": "0.8",
    }
    module = ModuleType("kittensynth")
    module.runtime_identity = lambda: dict(runtime_identity)
    monkeypatch.setitem(sys.modules, "kittensynth", module)

    def version(distribution: str) -> str:
        return {"kittensynth": "0.1.0"}[distribution]

    monkeypatch.setattr(importlib.metadata, "version", version)
    adapter = KittenSynthEngineAdapter()
    selection = EngineSelection(
        engine="kitten",
        target_id=DEFAULT_KITTEN_MODEL,
        language="en-us",
        voice="Jasper",
        options={"speed": 1.2, "cache_dir": "/transient/model-cache"},
        metadata={"source_revision": "a" * 40},
    )
    identity = adapter.canonical_synthesis_identity(selection)
    assert identity["engine"] == "kitten"
    assert identity["engine_version"] == "0.1.0"
    assert identity["engine_identity"] == runtime_identity
    assert identity["model_revision"] == "a" * 40
    assert identity["options"] == {"speed": 1.2}
    assert "/transient/model-cache" not in repr(identity)


def test_kitten_voice_catalog_uses_canonical_refs_and_unknown_gender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from readio.voices import discover_voice_catalog

    _install_fake_catalog(monkeypatch)
    entries, _result = discover_voice_catalog(engine="kitten", language="en", offline=True)
    assert len(entries) == 2
    assert entries[0].ref == "kitten:nano-0.8-int8/Jasper"
    assert entries[0].engine == "kitten"
    assert entries[0].gender == "unknown"
