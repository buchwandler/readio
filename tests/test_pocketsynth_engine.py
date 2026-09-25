from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import numpy as np
import pytest

try:
    import tomllib
except ImportError:
    import tomli as tomllib
from project_support import assert_neutral_session_contract
from utterplan import PlannerConfig, UtterancePlanner

from readio.engines.base import SpeechRequest
from readio.engines.catalog import CatalogRequest
from readio.engines.pocketsynth import PocketSynthEngineAdapter
from readio.engines.registry import engine_for_ssmd_provider, ssmd_provider_for_engine
from readio.engines.selection import EngineRequest
from readio.rendering.lowering import LoweringError, lower_segment


class _Bundle:
    id = "english-2026"
    aliases = ("english",)
    sample_rate = 24000
    voices = ("alba", "bella")
    metadata: ClassVar[dict[str, Any]] = {
        "language": "en",
        "predefined_voice_names": ["alba", "bella"],
        "profiles": {"fp32": {}, "int8": {}},
        "source_revision": "revision-1",
    }


class _AssetManager:
    bundles: ClassVar[tuple[_Bundle, ...]] = (_Bundle(),)
    instances: ClassVar[list[Any]] = []
    resolve_calls: ClassVar[list[Any]] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.instances.append(self)

    def list_bundles(self, *, language=None, refresh=False):
        self.list_request = (language, refresh)
        return self.bundles

    def resolve_bundle(self, bundle, **kwargs):
        self.resolve_calls.append((bundle, kwargs))
        return self.bundles[0]


class _Frontend:
    def split_for_model(self, text):
        return (text[:5], text[5:])

    def encode(self, text):
        return tuple(ord(character) for character in text)


class _Runtime:
    def __init__(self):
        self.metadata = SimpleNamespace(language="en")
        self.sample_rate = 24000
        self.frontend = _Frontend()
        self.prepared_sources = []
        self.inference_calls = []
        self.close_calls = 0

    def prepare_voice(self, source):
        self.prepared_sources.append(source)
        return f"prepared:{source}"

    def infer_tokens(self, tokens, voice, generation):
        self.inference_calls.append((tokens, voice, generation))
        return np.full(2, len(tokens), dtype=np.float32)

    def close(self):
        self.close_calls += 1


def _request(*, language="en-us", voice="alba", options=None):
    return EngineRequest(
        engine="pocket",
        target_id="english-2026",
        language=language,
        voice=voice,
        options=options or {},
    )


def _install_fakes(monkeypatch, runtime=None):
    pocketsynth = pytest.importorskip("pocketsynth")
    _AssetManager.instances = []
    _AssetManager.resolve_calls = []
    monkeypatch.setattr(pocketsynth, "BundleAssetManager", _AssetManager)
    runtime = runtime or _Runtime()
    monkeypatch.setattr(
        pocketsynth.PocketRuntime,
        "from_resolved",
        classmethod(lambda cls, bundle, **kwargs: runtime),
    )
    return pocketsynth, runtime


def test_pocket_discovery_maps_bundle_catalog(monkeypatch):
    _install_fakes(monkeypatch)
    targets = PocketSynthEngineAdapter().discover(CatalogRequest(engine="pocket", language="en-us"))

    assert len(targets) == 1
    assert targets[0].id == "english-2026"
    assert targets[0].languages == ("en",)
    assert targets[0].sample_rate == 24000
    assert targets[0].qualities == ("fp32", "int8")
    assert targets[0].voices == ("alba", "bella")
    assert _AssetManager.instances[-1].list_request == ("en-us", False)
    assert engine_for_ssmd_provider("pocket") == "pocket"
    assert ssmd_provider_for_engine("pocket") == "pocket"


def test_pocket_resolves_precision_and_rejects_incompatible_language(monkeypatch):
    _install_fakes(monkeypatch)
    adapter = PocketSynthEngineAdapter()
    selection, _ = adapter.resolve(_request(options={"quality": "fp32"}))

    assert selection.options["precision"] == "fp32"
    assert adapter.validate_selection(selection) == ()
    incompatible = replace(selection, language="de-de")
    diagnostics = adapter.validate_selection(incompatible)
    assert [item.code for item in diagnostics] == ["engine_language_incompatible"]
    invalid_precision = replace(selection, options={**selection.options, "precision": "bf16"})
    precision_diagnostics = adapter.validate_selection(invalid_precision)
    assert [item.code for item in precision_diagnostics] == ["pocket.precision_unavailable"]


def test_pocket_generation_options_validate_before_catalog_or_runtime(monkeypatch):
    _, runtime = _install_fakes(monkeypatch)
    adapter = PocketSynthEngineAdapter()
    selection, _ = adapter.resolve(_request(options={"temperature": "not-a-number"}))

    diagnostics = adapter.validate_selection(selection)

    assert [item.code for item in diagnostics] == ["pocket.generation_config_invalid"]
    assert _AssetManager.instances == []
    assert runtime.close_calls == 0


def test_pocket_session_reuses_prepared_voices_and_maps_generation_controls(monkeypatch):
    pocketsynth, runtime = _install_fakes(monkeypatch)
    adapter = PocketSynthEngineAdapter()
    selection, _ = adapter.resolve(
        _request(
            options={
                "quality": "fp32",
                "temperature": 0.9,
                "lsd_steps": 3,
                "max_frames": 120,
                "frames_after_eos": 8,
            }
        )
    )

    with adapter.open(selection) as session:
        requests = (
            SpeechRequest(id="seg-1", text="Hello world", language="en-us", voice="alba"),
            SpeechRequest(id="seg-2", text="Hello again", language="en-us", voice="bella"),
            SpeechRequest(id="seg-3", text="Hello once more", language="en-us", voice="alba"),
        )
        rendered = [assert_neutral_session_contract(session, request) for request in requests]

    assert [result.id for result in rendered] == ["seg-1", "seg-2", "seg-3"]
    assert all(result.sample_rate == 24000 for result in rendered)
    assert rendered[0].audio.tolist() == [5.0, 5.0, 6.0, 6.0]
    assert rendered[0].metadata["precision"] == "fp32"
    assert rendered[0].metadata["chunks"] == 2
    assert runtime.prepared_sources == ["alba", "bella"]
    assert len(runtime.inference_calls) == 6
    assert [call[1] for call in runtime.inference_calls] == [
        "prepared:alba",
        "prepared:alba",
        "prepared:bella",
        "prepared:bella",
        "prepared:alba",
        "prepared:alba",
    ]
    generation = runtime.inference_calls[0][2]
    assert isinstance(generation, pocketsynth.GenerationConfig)
    assert generation.temperature == 0.9
    assert generation.lsd_steps == 3
    assert generation.max_frames == 120
    assert generation.frames_after_eos == 8
    assert runtime.close_calls == 1
    assert _AssetManager.resolve_calls[0][1]["precision"] == "fp32"


def test_pocket_reference_voice_is_content_identified_and_cached(monkeypatch, tmp_path):
    _install_fakes(monkeypatch)
    adapter = PocketSynthEngineAdapter()
    voice_path = tmp_path / "reference.wav"
    voice_path.write_bytes(b"reference voice bytes")
    digest = hashlib.sha256(voice_path.read_bytes()).hexdigest()
    source = {"kind": "reference", "value": str(voice_path), "sha256": digest}
    selection, _ = adapter.resolve(_request(voice=None, options={"voice_source": source}))
    diagnostics = adapter.validate_selection(selection)

    assert diagnostics == ()
    metadata = adapter.target_metadata(selection)
    assert metadata["voice_source"] == source
    identity = adapter.canonical_synthesis_identity(replace(selection, metadata=metadata))
    other_path = tmp_path / "same-content.wav"
    other_path.write_bytes(voice_path.read_bytes())
    other_source = {**source, "value": str(other_path)}
    other_identity = adapter.canonical_synthesis_identity(
        replace(selection, metadata={"voice_source": other_source})
    )
    assert identity["voice"] == {"kind": "reference", "sha256": digest}
    assert identity["voice"] == other_identity["voice"]

    runtime = _Runtime()
    _pocketsynth, runtime = _install_fakes(monkeypatch, runtime)
    named_source = {"kind": "named", "value": "bella"}
    with adapter.open(replace(selection, metadata=metadata)) as session:
        assert_neutral_session_contract(
            session, SpeechRequest(id="reference-1", text="Hello", language="en-us")
        )
        assert_neutral_session_contract(
            session,
            SpeechRequest(
                id="named",
                text="Using another voice",
                language="en-us",
                voice="bella",
                options={"voice_source": named_source},
            ),
        )
        assert_neutral_session_contract(
            session, SpeechRequest(id="reference-2", text="Again", language="en-us")
        )
    assert runtime.prepared_sources == [voice_path, "bella"]
    assert runtime.close_calls == 1


def test_pocket_rejects_unrepresentable_pronunciation_directive(monkeypatch):
    _install_fakes(monkeypatch)
    adapter = PocketSynthEngineAdapter()
    plan = UtterancePlanner(
        PlannerConfig(
            language="en-us",
            document_format="ssmd",
            text_preparation="identity",
            unit="sentence",
        )
    ).plan('[tomato]{ph="təˈmeɪtoʊ" alphabet="ipa"}')
    selection, _ = adapter.resolve(_request())

    capabilities = replace(adapter.capabilities(), pronunciation_alphabets=frozenset({"ipa"}))
    with pytest.raises(LoweringError) as error:
        lower_segment(
            plan,
            plan.segments[0],
            selection,
            capabilities,
        )

    assert error.value.diagnostic.code == "render.pronunciation_unsupported"


def test_pocket_extra_uses_a_published_runtime_release():
    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    extras = pyproject["project"]["optional-dependencies"]

    assert extras["pocket"] == ["pocketsynth[cpu]>=0.1.0,<0.2"]
    assert any("pocketsynth[cpu]>=0.1.0,<0.2" in item for item in extras["all"])
