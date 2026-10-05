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

from readio.engines.base import EngineSelection, SpeechRequest
from readio.engines.catalog import CatalogRequest, TargetVoice
from readio.engines.pocketsynth import PocketSynthEngineAdapter
from readio.engines.registry import engine_for_ssmd_provider, ssmd_provider_for_engine
from readio.engines.selection import EngineRequest
from readio.errors import EngineBackendError
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
    def encode(self, text):
        return tuple(ord(character) for character in text)

    def split_for_model(self, text):
        raise AssertionError("Readio must not call PocketSynth's text splitter")


class _Runtime:
    def __init__(self):
        self.metadata = SimpleNamespace(language="en", max_token_per_chunk=192)
        self.sample_rate = 24000
        self.frontend = _Frontend()
        self.prepared_sources = []
        self.synthesis_calls = []
        self.close_calls = 0

    def prepare_voice(self, source):
        self.prepared_sources.append(source)
        return f"prepared:{source}"

    def synthesize(self, request, *, voice, config, voice_level):
        self.synthesis_calls.append((request, voice, config, voice_level))
        return SimpleNamespace(
            id=request.id,
            audio=np.full(2, len(request.text), dtype=np.float32),
            sample_rate=self.sample_rate,
            warnings=(),
            metadata={
                "chunks": 1,
                "voice_level_application": {
                    "mode": voice_level.mode,
                    "applied": False,
                    "source": "none",
                },
            },
        )

    def close(self):
        self.close_calls += 1


class _PublicRuntime:
    def __init__(self):
        self.sample_rate = 24000
        self.measure_calls = []
        self.synthesis_calls = []
        self.prepared_sources = []
        self.close_calls = 0

    def measure_request(self, request):
        self.measure_calls.append(request)
        return SimpleNamespace(fits=True, amount=len(request.text), maximum=192)

    def prepare_voice(self, source):
        self.prepared_sources.append(source)
        return f"prepared:{source}"

    def synthesize(self, request, *, voice, config, voice_level):
        self.synthesis_calls.append((request, voice, config, voice_level))
        return SimpleNamespace(
            id=request.id,
            audio=np.ones(2, dtype=np.float32),
            sample_rate=self.sample_rate,
            warnings=(),
            metadata={"token_count": len(request.text)},
        )

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

    def discover_bundles(
        *, language=None, offline=False, refresh=False, cache_dir=None, catalog_path=None
    ):
        manager = _AssetManager(
            cache_dir=cache_dir,
            catalog_path=catalog_path,
            offline=offline,
        )
        return manager.list_bundles(language=language, refresh=refresh)

    monkeypatch.setattr(pocketsynth, "discover_bundles", discover_bundles, raising=False)
    monkeypatch.setattr(
        pocketsynth,
        "runtime_identity",
        lambda: {"request_api_version": "1", "runtime_revision": "test-runtime"},
        raising=False,
    )
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
    assert _AssetManager.instances[-1].list_request == ("en", False)
    assert engine_for_ssmd_provider("pocket") == "pocket"
    assert ssmd_provider_for_engine("pocket") == "pocket"

    assert targets[0].voice_details == ()


def test_pocket_discovery_uses_normalized_public_bundles(monkeypatch):
    pocketsynth, _runtime = _install_fakes(monkeypatch)
    bundle = SimpleNamespace(
        id="normalized-pocket",
        ref="pocket:normalized-pocket",
        display_name="Normalized Pocket bundle",
        aliases=("normalized-alias",),
        language="en-US",
        sample_rate=22050,
        precisions=("int8", "fp32"),
        predefined_voices=("ava",),
        default_voice="ava",
        source_revision="public-revision",
        max_tokens=96,
        runtime_available=True,
        voice_details=(
            SimpleNamespace(
                id="ava",
                gender="female",
                language="en",
                locale="en-US",
                language_label="English",
            ),
        ),
        metadata={"catalog_source": "normalized"},
    )
    calls = []

    def discover_bundles(**kwargs):
        calls.append(kwargs)
        return (bundle,)

    def fail_list_bundles(*args, **kwargs):
        raise AssertionError("new Pocket discovery must not call BundleAssetManager.list_bundles")

    monkeypatch.setattr(pocketsynth, "discover_bundles", discover_bundles)
    monkeypatch.setattr(_AssetManager, "list_bundles", fail_list_bundles)

    adapter = PocketSynthEngineAdapter()
    targets = adapter.discover(
        CatalogRequest(engine="pocket", language="en-us", offline=True, refresh=True)
    )

    assert calls[0] == {
        "language": "en",
        "offline": True,
        "refresh": True,
        "cache_dir": None,
        "catalog_path": None,
    }
    assert len(targets) == 1
    target = targets[0]
    assert target.id == "normalized-pocket"
    assert target.display_name == "Normalized Pocket bundle"
    assert target.aliases == ("normalized-alias",)
    assert target.languages == ("en-us",)
    assert target.sample_rate == 22050
    assert target.voices == ("ava",)
    assert target.default_voice == "ava"
    assert target.qualities == ("int8", "fp32")
    assert target.runtime_available
    assert target.metadata["source_revision"] == "public-revision"
    assert target.metadata["max_token_per_chunk"] == 96
    assert target.voice_details[0] == TargetVoice(
        id="ava",
        gender="female",
        language="en",
        locale="en-us",
        language_label="English",
    )

    selection, _ = adapter.resolve(_request(voice="ava"))
    selection = replace(selection, target_id="normalized-pocket")
    assert adapter.validate_selection(selection) == ()
    metadata = adapter.target_metadata(selection)
    assert metadata["source_revision"] == "public-revision"
    assert metadata["max_token_per_chunk"] == 96
    assert _AssetManager.instances == []


def test_pocket_discovery_includes_generic_language_but_excludes_other_region(monkeypatch):
    _install_fakes(monkeypatch)
    british_bundle = SimpleNamespace(
        id="english-gb",
        aliases=(),
        sample_rate=24000,
        voices=("british",),
        metadata={
            "language": "en-GB",
            "predefined_voice_names": ["british"],
            "profiles": {"int8": {}},
        },
    )
    monkeypatch.setattr(_AssetManager, "bundles", (_Bundle(), british_bundle))

    targets = PocketSynthEngineAdapter().discover(CatalogRequest(engine="pocket", language="en-us"))

    assert [target.id for target in targets] == ["english-2026"]
    assert targets[0].languages == ("en",)
    assert _AssetManager.instances[-1].list_request == ("en", False)


def test_pocket_discovery_normalizes_bundle_and_voice_details(monkeypatch):
    _install_fakes(monkeypatch)
    bundle = SimpleNamespace(
        id="english-us",
        aliases=(),
        sample_rate=24000,
        voices=("alba", "bella"),
        metadata={
            "language": "en_US",
            "predefined_voice_names": ["alba", "bella"],
            "profiles": {"int8": {}},
            "voice_details": [
                {
                    "id": "alba",
                    "language": "en",
                    "locale": "en",
                    "language_label": "English",
                    "gender": "female",
                },
                {
                    "id": "bella",
                    "language": "en_GB",
                    "locale": "en_GB",
                    "language_label": "GB",
                    "gender": None,
                },
            ],
        },
    )
    monkeypatch.setattr(_AssetManager, "bundles", (bundle,))

    targets = PocketSynthEngineAdapter().discover(CatalogRequest(engine="pocket", language="en-us"))

    assert len(targets) == 1
    assert targets[0].languages == ("en-us",)
    assert targets[0].metadata["language"] == "en-us"
    assert targets[0].voice_details == (
        TargetVoice(
            id="alba",
            gender="female",
            language="en",
            locale="en",
            language_label="English",
        ),
        TargetVoice(
            id="bella",
            gender="unknown",
            language="en",
            locale="en-gb",
            language_label="en-gb",
        ),
    )


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


def test_managed_prompt_resolution_uses_public_metadata_without_opening_runtime(monkeypatch):
    pocketsynth, runtime = _install_fakes(monkeypatch)
    prompt_ref = "kyutai-tts-voices:alba/casual"
    prompt_info = SimpleNamespace(
        ref=prompt_ref,
        sha256="a" * 64,
        source_revision="catalog-rev-3",
        source_repository="kyutai/voices",
        source_path="alba/casual.wav",
        license="cc-by-4.0",
        dataset="alba",
        variant="casual",
    )
    calls = []

    def inspect_voice_prompt(ref, **options):
        calls.append((ref, options))
        return prompt_info

    monkeypatch.setattr(pocketsynth, "inspect_voice_prompt", inspect_voice_prompt)
    request = replace(
        _request(
            voice=None,
            options={
                "voice_source": {"kind": "managed_reference", "value": prompt_ref},
                "cache_dir": "prompt-cache",
                "catalog_path": "prompt-catalog.json",
            },
        ),
        offline=True,
        refresh=True,
    )
    selection, _ = PocketSynthEngineAdapter().resolve(request)

    assert calls == [
        (
            prompt_ref,
            {
                "cache_dir": "prompt-cache",
                "catalog_path": "prompt-catalog.json",
                "offline": True,
                "refresh": True,
            },
        )
    ]
    assert selection.metadata["voice_source"] == {
        "kind": "managed_reference",
        "value": prompt_ref,
        "sha256": "a" * 64,
        "source_revision": "catalog-rev-3",
        "source_repository": "kyutai/voices",
        "source_path": "alba/casual.wav",
        "license": "cc-by-4.0",
        "dataset": "alba",
        "variant": "casual",
    }
    assert _AssetManager.instances == []
    assert runtime.close_calls == 0


def test_pocket_prompt_listing_uses_metadata_api_without_runtime_or_audio_fetch(monkeypatch):
    pocketsynth, runtime = _install_fakes(monkeypatch)
    prompt = SimpleNamespace(
        ref="kyutai-tts-voices:alba/casual",
        source_repository="kyutai/voices",
        source_revision="catalog-rev-3",
        source_path="alba/casual.wav",
        size=1234,
        sha256="a" * 64,
        license="cc-by-4.0",
        dataset="alba",
        variant="casual",
    )
    calls = []

    def list_voice_prompts(**kwargs):
        calls.append(kwargs)
        return (prompt,)

    monkeypatch.setattr(pocketsynth, "list_voice_prompts", list_voice_prompts)
    prompts = PocketSynthEngineAdapter().list_voice_prompts(
        dataset="alba",
        variant="casual",
        license="cc-by-4.0",
        offline=True,
        refresh=True,
    )

    assert prompts == (prompt,)
    assert calls == [
        {
            "dataset": "alba",
            "variant": "casual",
            "license": "cc-by-4.0",
            "offline": True,
            "refresh": True,
        }
    ]
    assert _AssetManager.instances == []
    assert runtime.prepared_sources == []
    assert runtime.close_calls == 0


def test_pocket_generation_options_validate_before_catalog_or_runtime(monkeypatch):
    _, runtime = _install_fakes(monkeypatch)
    adapter = PocketSynthEngineAdapter()
    selection, _ = adapter.resolve(_request(options={"temperature": "not-a-number"}))

    diagnostics = adapter.validate_selection(selection)

    assert [item.code for item in diagnostics] == ["pocket.generation_config_invalid"]
    assert _AssetManager.instances == []
    assert runtime.close_calls == 0


def test_pocket_session_reuses_prepared_voices_and_uses_one_strict_call_per_request(
    monkeypatch,
):
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
    assert rendered[0].audio.tolist() == [11.0, 11.0]
    assert rendered[0].metadata["precision"] == "fp32"
    assert rendered[0].metadata["chunks"] == 1
    assert rendered[0].metadata["token_count"] == len("Hello world")
    assert rendered[0].metadata["voice_level"]["mode"] == "off"
    assert runtime.prepared_sources == ["alba", "bella"]
    assert len(runtime.synthesis_calls) == 3
    assert [call[0].text for call in runtime.synthesis_calls] == [
        "Hello world",
        "Hello again",
        "Hello once more",
    ]
    assert [call[1] for call in runtime.synthesis_calls] == [
        "prepared:alba",
        "prepared:bella",
        "prepared:alba",
    ]
    generation = runtime.synthesis_calls[0][2]
    assert isinstance(generation, pocketsynth.GenerationConfig)
    assert generation.temperature == 0.9
    assert generation.lsd_steps == 3
    assert generation.max_frames == 120
    assert generation.frames_after_eos == 8
    assert isinstance(runtime.synthesis_calls[0][3], pocketsynth.VoiceLevelConfig)
    assert runtime.close_calls == 1
    assert _AssetManager.resolve_calls[0][1]["precision"] == "fp32"


def test_pocket_public_measurement_uses_shared_native_request_without_runtime_internals(
    monkeypatch,
):
    pocketsynth, runtime = _install_fakes(monkeypatch, _PublicRuntime())
    adapter = PocketSynthEngineAdapter()
    selection, _ = adapter.resolve(_request())
    request = SpeechRequest(
        id="public-measure",
        text="Hello Pocket",
        language="en-us",
        voice="alba",
    )

    with adapter.open(selection) as session:
        measured = session.measure(request)
        assert len(runtime.measure_calls) == 1
        assert runtime.synthesis_calls == []
        rendered = session.synthesize(request)

    assert measured.fits is True
    assert measured.amount == len(request.text)
    assert measured.maximum == 192
    assert measured.unit == "model_tokens"
    assert measured.source == "pocketsynth.measure_request"
    assert len(runtime.measure_calls) == 1
    measured_request = runtime.measure_calls[0]
    synthesized_request = runtime.synthesis_calls[0][0]
    assert isinstance(measured_request, pocketsynth.SynthesisRequest)
    assert measured_request == synthesized_request
    assert not hasattr(runtime, "frontend")
    assert not hasattr(runtime, "metadata")
    assert rendered.metadata["token_count"] == len(request.text)
    assert runtime.close_calls == 1


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
    calibrated_identity = adapter.canonical_synthesis_identity(
        replace(
            selection,
            options={**selection.options, "voice_level": "calibrated"},
        )
    )
    assert identity["voice_level"] == "off"
    assert calibrated_identity["voice_level"] == "calibrated"
    assert identity != calibrated_identity

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


def test_pocket_managed_prompt_uses_pinned_identity_cache_and_preserves_fingerprint(monkeypatch):
    _pocketsynth, runtime = _install_fakes(monkeypatch)
    source_a = {
        "kind": "managed_reference",
        "value": "kyutai-tts-voices:alba/casual",
        "sha256": "a" * 64,
        "source_revision": "catalog-rev-3",
    }
    source_b = {
        "kind": "managed_reference",
        "value": "kyutai-tts-voices:bella/bright",
        "sha256": "b" * 64,
        "source_revision": "catalog-rev-4",
    }
    selection = EngineSelection(
        engine="pocket",
        target_id="english-2026",
        language="en-us",
        metadata={"voice_source": source_a},
        offline=True,
    )
    sources = {source["value"]: source for source in (source_a, source_b)}

    def prepare_voice(ref):
        runtime.prepared_sources.append(ref)
        source = sources[ref]
        return SimpleNamespace(
            metadata={
                "kind": "managed_reference",
                "managed_ref": ref,
                "source_sha256": source["sha256"],
                "source_revision": source["source_revision"],
            },
            voice_prompt=SimpleNamespace(
                ref=ref,
                sha256=source["sha256"],
                source_revision=source["source_revision"],
            ),
            fingerprint=f"prepared:{source['sha256']}",
        )

    monkeypatch.setattr(runtime, "prepare_voice", prepare_voice)
    adapter = PocketSynthEngineAdapter()
    with adapter.open(selection) as session:
        rendered = [
            session.synthesize(
                SpeechRequest(
                    id=f"managed-{index}",
                    text="Managed prompt",
                    language="en-us",
                    options={"voice_source": source},
                )
            )
            for index, source in enumerate((source_a, source_a, source_b))
        ]

    identity = adapter.canonical_synthesis_identity(selection)["voice"]
    assert identity == {
        "kind": "managed_reference",
        "ref": source_a["value"],
        "sha256": source_a["sha256"],
        "source_revision": source_a["source_revision"],
    }
    assert runtime.prepared_sources == [source_a["value"], source_b["value"]]
    assert len(runtime.synthesis_calls) == 3
    assert rendered[0].metadata["voice"] == identity
    assert rendered[0].metadata["prepared_voice_fingerprint"] == f"prepared:{source_a['sha256']}"
    assert rendered[2].metadata["prepared_voice_fingerprint"] == f"prepared:{source_b['sha256']}"


def test_pocket_managed_prompt_rejects_changed_prepared_provenance(monkeypatch):
    _pocketsynth, runtime = _install_fakes(monkeypatch)
    source = {
        "kind": "managed_reference",
        "value": "kyutai-tts-voices:alba/casual",
        "sha256": "a" * 64,
        "source_revision": "catalog-rev-3",
    }
    selection = EngineSelection(
        engine="pocket",
        target_id="english-2026",
        language="en-us",
        metadata={"voice_source": source},
    )
    monkeypatch.setattr(
        runtime,
        "prepare_voice",
        lambda _ref: SimpleNamespace(
            metadata={
                "managed_ref": source["value"],
                "source_sha256": "b" * 64,
                "source_revision": source["source_revision"],
            },
            voice_prompt=None,
            fingerprint="prepared",
        ),
    )

    with (
        PocketSynthEngineAdapter().open(selection) as session,
        pytest.raises(EngineBackendError) as error,
    ):
        session.synthesize(SpeechRequest(id="changed", text="Changed prompt", language="en-us"))
    assert error.value.code == "pocket.managed_reference_changed"
    assert error.value.details["expected_sha256"] == source["sha256"]
    assert error.value.details["actual_sha256"] == "b" * 64


def test_pocket_managed_prompt_reports_stable_offline_cache_failure(monkeypatch):
    _pocketsynth, runtime = _install_fakes(monkeypatch)
    source = {
        "kind": "managed_reference",
        "value": "kyutai-tts-voices:alba/casual",
        "sha256": "a" * 64,
        "source_revision": "catalog-rev-3",
    }
    selection = EngineSelection(
        engine="pocket",
        target_id="english-2026",
        language="en-us",
        metadata={"voice_source": source},
        offline=True,
    )

    def fail_prepare(_ref):
        raise RuntimeError("prompt file is not cached")

    monkeypatch.setattr(runtime, "prepare_voice", fail_prepare)
    with (
        PocketSynthEngineAdapter().open(selection) as session,
        pytest.raises(EngineBackendError) as error,
    ):
        session.synthesize(SpeechRequest(id="offline", text="Offline prompt", language="en-us"))

    assert error.value.code == "pocket.voice_prompt_offline_unavailable"
    assert error.value.details["voice_prompt"] == source["value"]
    assert error.value.details["offline"] is True


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

    assert extras["pocket"] == ["pocketsynth[cpu]>=0.2.3,<0.3"]
    assert any("pocketsynth[cpu]>=0.2.3,<0.3" in item for item in extras["all"])


def test_pocket_canonical_identity_uses_public_runtime_identity(monkeypatch):
    pocketsynth, _runtime = _install_fakes(monkeypatch)
    runtime_identity = {
        "engine_version": "0.2.4",
        "runtime_revision": "onnxvoice-rev",
        "request_api_version": "1",
        "bundle_revision": None,
    }
    monkeypatch.setattr(pocketsynth, "runtime_identity", lambda: dict(runtime_identity))
    selection, _ = PocketSynthEngineAdapter().resolve(_request())

    identity = PocketSynthEngineAdapter().canonical_synthesis_identity(selection)

    assert identity["engine_identity"] == runtime_identity
    assert identity["target_id"] == selection.target_id
