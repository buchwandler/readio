from __future__ import annotations

import sys
from dataclasses import dataclass
from types import ModuleType, SimpleNamespace

import numpy as np
from project_support import assert_neutral_session_contract

from readio.engines import EngineSelection, SpeechRequest
from readio.engines.catalog import CatalogRequest
from readio.engines.pipersynth import PiperSynthEngineAdapter
from readio.engines.registry import CANONICAL_ENGINE_IDS, normalize_engine_id
from readio.engines.selection import EngineRequest


def test_piper_identity_capabilities_and_supported_options():
    assert normalize_engine_id("pipersynth") == "piper"
    assert "piper" in CANONICAL_ENGINE_IDS

    capabilities = PiperSynthEngineAdapter().capabilities()
    assert capabilities.id == "piper"
    assert capabilities.voice_binding_namespace == "piper"
    assert capabilities.voice_binding_scope == "target"
    assert capabilities.supports_live
    assert capabilities.supports_speakers
    assert not capabilities.supports_lexicons
    assert {"length_scale", "noise_scale", "noise_w_scale"} <= capabilities.option_names


def test_piper_discovery_maps_published_voice_metadata(monkeypatch):

    metadata = SimpleNamespace(
        id="de_DE-thorsten-medium",
        name="Thorsten",
        language_code="de-DE",
        language_family="de",
        region="DE",
        quality="medium",
        num_speakers=2,
        speaker_id_map={"narrator": 0, "announcer": 1},
        aliases=("thorsten",),
        source_revision="rev-1",
    )
    calls = {}

    class VoiceAssetManager:
        def __init__(self, *, offline):
            calls["offline"] = offline

        def list_voices(self, *, language, refresh):
            calls.update(language=language, refresh=refresh)
            return (metadata,)

    monkeypatch.setattr("pipersynth.asset_manager.VoiceAssetManager", VoiceAssetManager)
    targets = PiperSynthEngineAdapter().discover(
        CatalogRequest(engine="piper", language="de", offline=True, refresh=True)
    )

    assert calls == {"offline": True, "language": "de", "refresh": True}
    assert len(targets) == 1
    assert targets[0].id == metadata.id
    assert targets[0].languages == ("de-DE",)
    assert targets[0].speakers == ("narrator", "announcer")
    assert targets[0].qualities == ("medium",)
    assert targets[0].metadata["source_revision"] == "rev-1"


def test_piper_resolution_maps_generic_speed_to_length_scale():
    selection, diagnostics = PiperSynthEngineAdapter().resolve(
        EngineRequest(
            engine="piper",
            target_id="en_US-lessac-medium",
            language="en-us",
            voice="lessac",
            options={"speed": 2.0, "noise_scale": 0.5, "spacy": "lg"},
            engine_options={"noise_w_scale": 0.25},
        )
    )

    assert not diagnostics
    assert selection.target_id == "en_US-lessac-medium"
    assert selection.options == {
        "length_scale": 0.5,
        "noise_scale": 0.5,
        "noise_w_scale": 0.25,
    }


def test_piper_published_text_session_uses_the_neutral_contract(monkeypatch):
    module = ModuleType("pipersynth")
    calls = {}

    @dataclass(frozen=True)
    class Config:
        length_scale: float | None = None
        noise_scale: float | None = None
        noise_w_scale: float | None = None
        normalize_audio: bool = True
        volume: float = 1.0
        loudness: object | None = None
        speaker_id: int | None = None

    class Voice:
        config = SimpleNamespace(sample_rate=22050)

        @classmethod
        def from_pretrained(cls, voice_id, **kwargs):
            calls["voice_id"] = voice_id
            calls["load_options"] = kwargs
            return cls()

        def resolve_speaker_id(self, speaker):
            calls["speaker"] = speaker
            return 0

        def synthesize(self, text, syn_config):
            calls["text"] = text
            calls["config"] = syn_config
            return iter(
                [
                    SimpleNamespace(
                        audio_float_array=np.array([0.2, -0.2], dtype=np.float32),
                        sample_rate=22050,
                        phonemes=("h", "i"),
                        phoneme_ids=(1, 2),
                        warnings=(),
                    )
                ]
            )

        def close(self):
            calls["closed"] = True

    module.PiperVoice = Voice
    module.SynthesisConfig = Config
    module.LoudnessConfig = Config
    monkeypatch.setitem(sys.modules, "pipersynth", module)

    selection = EngineSelection(
        engine="piper",
        target_id="en_US-lessac-medium",
        language="en-us",
        voice="lessac",
        speaker="narrator",
        options={"length_scale": 0.5, "noise_scale": 0.4},
    )
    request = SpeechRequest(
        id="piper-request",
        text="Hi",
        language="en-us",
        voice="lessac",
        speaker="narrator",
    )

    with PiperSynthEngineAdapter().open(selection) as session:
        rendered = assert_neutral_session_contract(session, request)

    assert calls["voice_id"] == selection.target_id
    assert calls["text"] == "Hi"
    assert calls["speaker"] == "narrator"
    assert calls["config"].length_scale == 0.5
    assert calls["config"].noise_scale == 0.4
    assert rendered.metadata["phonemes"] == ("h", "i")
    assert calls["closed"]
