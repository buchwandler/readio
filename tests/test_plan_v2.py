"""Tests for the engine-neutral readio.plan.v2 schema."""

from __future__ import annotations

from dataclasses import replace

import pytest
from utterplan import CURRENT_SCHEMA_VERSION

from readio.plan import (
    EnvironmentPlanV2,
    PlanningPlanV2,
    ReadioPlanV2,
    RenderPlanV2,
    RenderTargetV2,
    RoleTargetBindingV2,
    SemanticPlanRef,
    VoiceSourceV2,
    render_identity,
)


def test_semantic_plan_ref_uses_utterplan_schema() -> None:
    ref = SemanticPlanRef(plan_id="plan", sha256="hash")
    assert ref.format == "utterplan"
    assert ref.schema_version == CURRENT_SCHEMA_VERSION == 4
    assert ref.to_dict()["sha256"] == "hash"


def test_voice_source_has_explicit_kind_and_reference_hash() -> None:
    named = VoiceSourceV2(kind="named", value="af_sarah")
    assert named.to_dict() == {"kind": "named", "value": "af_sarah"}
    reference = VoiceSourceV2(kind="reference", value="voices/guest.wav", sha256="abc")
    assert reference.to_dict() == {
        "kind": "reference",
        "value": "voices/guest.wav",
        "sha256": "abc",
    }
    with pytest.raises(ValueError, match="stable SHA-256"):
        VoiceSourceV2(kind="reference", value="voices/guest.wav")


def test_managed_voice_source_round_trips_provenance_and_identity():
    from readio.plan import VoiceSourceV2

    source = VoiceSourceV2(
        kind="managed_reference",
        value="kyutai-tts-voices:alba/casual",
        sha256="a" * 64,
        source_revision="catalog-rev-3",
        source_repository="kyutai/voices",
        source_path="alba/casual.wav",
        license="cc-by-4.0",
        dataset="alba",
        variant="casual",
    )
    target = RenderTargetV2(id="pocket-model", language="en", voice=source)
    restored = RenderTargetV2.from_dict(target.to_dict())
    assert restored == target
    assert restored.voice.to_dict() == source.to_dict()

    from readio.engines.base import EngineSelection
    from readio.engines.selection import engine_selection_from_render_plan
    from readio.execution import _selection_for_target

    render_plan = RenderPlanV2(engine="pocket", default_target=target)
    selected = engine_selection_from_render_plan(render_plan)
    assert selected.voice is None
    assert selected.metadata["voice_source"] == source.to_dict()
    handoff = _selection_for_target(
        EngineSelection(engine="pocket", target_id="pocket-model", language="en"),
        target,
    )
    assert handoff.voice is None
    assert handoff.metadata["voice_source"] == source.to_dict()

    render = RenderPlanV2(engine="pocket", default_target=target)
    identity = render_identity("sha256:semantic", render)
    assert identity != render_identity(
        "sha256:semantic",
        RenderPlanV2(
            engine="pocket",
            default_target=RenderTargetV2(
                id="pocket-model",
                language="en",
                voice=replace(source, sha256="b" * 64),
            ),
        ),
    )
    assert identity != render_identity(
        "sha256:semantic",
        RenderPlanV2(
            engine="pocket",
            default_target=RenderTargetV2(
                id="pocket-model",
                language="en",
                voice=replace(source, source_revision="catalog-rev-4"),
            ),
        ),
    )
    assert identity == render_identity(
        "sha256:semantic",
        RenderPlanV2(
            engine="pocket",
            default_target=RenderTargetV2(
                id="pocket-model",
                language="en",
                voice=replace(source, dataset="new-display-dataset"),
            ),
        ),
    )

    with pytest.raises(ValueError, match="unknown voice source kind"):
        VoiceSourceV2.from_dict({"kind": "future_source", "value": "x"})
    with pytest.raises(ValueError, match="stable SHA-256"):
        VoiceSourceV2.from_dict({"kind": "managed_reference", "value": "x"})


@pytest.mark.parametrize(
    "selectors",
    [
        {"voice": "named", "voice_file": "voice.wav"},
        {"voice": "named", "voice_prompt": "kyutai-tts-voices:alba/casual"},
        {"voice_file": "voice.wav", "voice_prompt": "kyutai-tts-voices:alba/casual"},
        {
            "voice": "named",
            "voice_file": "voice.wav",
            "voice_prompt": "kyutai-tts-voices:alba/casual",
        },
    ],
)
def test_synthesis_request_rejects_multiple_voice_sources(selectors) -> None:
    from readio.plan import SynthesisRequest

    with pytest.raises(ValueError, match="mutually exclusive"):
        SynthesisRequest(**selectors)


def test_render_target_serializes_typed_voice_source() -> None:
    target = RenderTargetV2(
        id="kokoro-model",
        language="en-us",
        voice=VoiceSourceV2(kind="named", value="af_sarah"),
        speaker=0,
        options={"quality": "high"},
    )
    assert target.to_dict() == {
        "id": "kokoro-model",
        "language": "en-us",
        "voice": {"kind": "named", "value": "af_sarah"},
        "speaker": 0,
        "options": {"quality": "high"},
    }


def test_render_plan_serializes_default_and_role_targets() -> None:
    default = RenderTargetV2(id="model", language="en-us")
    role_target = RenderTargetV2(
        id="model",
        language="en-us",
        voice=VoiceSourceV2(kind="named", value="voice-b"),
    )
    render = RenderPlanV2(
        engine="pykokoro",
        default_target=default,
        role_bindings=(
            RoleTargetBindingV2(
                role="narrator",
                target=role_target,
                origin="document",
                locator="ssmd.front_matter.voice_bindings",
            ),
        ),
        options={"quality": "high"},
    )
    data = render.to_dict()
    assert data["default_target"]["id"] == "model"
    assert data["role_bindings"][0]["role"] == "narrator"
    assert data["role_bindings"][0]["target"]["voice"] == {
        "kind": "named",
        "value": "voice-b",
    }
    assert data["options"] == {"quality": "high"}
    assert "target" not in data


def test_reference_voice_identity_is_explicit_and_stable() -> None:
    target = RenderTargetV2(
        id="pocket-bundle",
        language="en-us",
        voice=VoiceSourceV2(kind="reference", value="voices/guest.wav", sha256="abc"),
    )
    assert target.to_dict()["voice"]["sha256"] == "abc"


def test_environment_plan_uses_generic_package_metadata() -> None:
    environment = EnvironmentPlanV2(
        packages={"readio": "0.1.0", "utterplan": "0.1.2", "audiocompose": "0.2.0"},
        ffmpeg_available=True,
    )
    assert environment.to_dict()["packages"]["audiocompose"] == "0.2.0"
    assert environment.ffmpeg_available is True
    assert not hasattr(environment, "pykokoro_version")
    assert not hasattr(environment, "piper_version")


def test_planning_plan_serializes_typed_configuration() -> None:
    planning = PlanningPlanV2(
        language="en-us",
        unit="paragraph",
        text_preparation="spokenform",
        pause_mode="auto",
        spacy="auto",
    )
    assert planning.to_dict()["text_preparation"] == "spokenform"
    assert planning.to_dict()["spacy"] == "auto"


def test_readio_plan_v2_top_level_contract() -> None:
    render = RenderPlanV2(
        engine="piper",
        default_target=RenderTargetV2(id="voice", language="de"),
    )
    plan = ReadioPlanV2(render=render)
    data = plan.to_dict()
    assert data["schema"] == "readio.plan.v2"
    assert data["render"]["default_target"]["id"] == "voice"
    assert "environment" in data


def test_render_identity_includes_role_target_acoustics() -> None:
    semantic_sha = "semantic-sha"
    default = RenderTargetV2(id="model", language="en")
    first = RenderPlanV2(
        engine="pykokoro",
        default_target=default,
        role_bindings=(
            RoleTargetBindingV2(
                role="narrator",
                target=RenderTargetV2(
                    id="model", language="en", voice=VoiceSourceV2(kind="named", value="voice-a")
                ),
                origin="document",
            ),
        ),
    )
    same = RenderPlanV2(
        engine="pykokoro",
        default_target=default,
        role_bindings=(
            RoleTargetBindingV2(
                role="narrator",
                target=RenderTargetV2(
                    id="model", language="en", voice=VoiceSourceV2(kind="named", value="voice-a")
                ),
                origin="cli",
            ),
        ),
    )
    different = RenderPlanV2(
        engine="pykokoro",
        default_target=default,
        role_bindings=(
            RoleTargetBindingV2(
                role="narrator",
                target=RenderTargetV2(
                    id="model", language="en", voice=VoiceSourceV2(kind="named", value="voice-b")
                ),
                origin="document",
            ),
        ),
    )
    assert render_identity(semantic_sha, first) == render_identity(semantic_sha, same)
    assert render_identity(semantic_sha, first) != render_identity(semantic_sha, different)

    metadata_only_change = RenderPlanV2(
        engine="pykokoro",
        default_target=RenderTargetV2(
            id="model", language="en", metadata={"source": "other mirror"}
        ),
    )
    without_metadata = RenderPlanV2(
        engine="pykokoro",
        default_target=RenderTargetV2(id="model", language="en"),
    )
    assert render_identity(semantic_sha, metadata_only_change) == render_identity(
        semantic_sha, without_metadata
    )


def test_reference_voice_identity_uses_content_not_local_path() -> None:
    semantic_sha = "semantic-sha"
    first = RenderPlanV2(
        engine="pocket",
        default_target=RenderTargetV2(
            id="bundle",
            language="en",
            voice=VoiceSourceV2(
                kind="reference", value="/users/one/voice.wav", sha256="content-hash"
            ),
        ),
    )
    same_content = RenderPlanV2(
        engine="pocket",
        default_target=RenderTargetV2(
            id="bundle",
            language="en",
            voice=VoiceSourceV2(
                kind="reference", value="/users/two/voice.wav", sha256="content-hash"
            ),
        ),
    )
    different_content = RenderPlanV2(
        engine="pocket",
        default_target=RenderTargetV2(
            id="bundle",
            language="en",
            voice=VoiceSourceV2(
                kind="reference", value="/users/one/voice.wav", sha256="different-hash"
            ),
        ),
    )

    assert render_identity(semantic_sha, first) == render_identity(semantic_sha, same_content)
    assert render_identity(semantic_sha, first) != render_identity(semantic_sha, different_content)


def test_semantic_plan_compiles_when_engine_runtime_is_unavailable(monkeypatch) -> None:
    from readio.config import ReaderSettings, ReadioConfig
    from readio.document import document_from_text
    from readio.engines import registry
    from readio.plan import (
        InputRequest,
        OutputRequest,
        PlanRequest,
        SynthesisRequest,
        resolve_execution_v2,
    )

    def unavailable_engine(_engine: str):
        raise ValueError("engine runtime unavailable")

    monkeypatch.setattr(registry, "get_engine", unavailable_engine)
    request = PlanRequest(
        operation="render",
        input=InputRequest(document=document_from_text("Plan this without a runtime.")),
        synthesis=SynthesisRequest(
            engine="missing-engine", voice_prompt="kyutai-tts-voices:alba/casual"
        ),
        output=OutputRequest(),
    )

    resolved = resolve_execution_v2(
        ReadioConfig(reader=ReaderSettings(engine="missing-engine")),
        request,
    )

    assert resolved.semantic is not None
    assert resolved.plan.semantic_plan.plan_id == resolved.semantic.plan_id
    assert resolved.plan.render is None
    assert any(item.code == "engine_adapter_unavailable" for item in resolved.plan.diagnostics)

    assert any(item.code == "voice_source_unsupported" for item in resolved.plan.diagnostics)


def test_semantic_plan_compiles_but_skips_incompatible_engine_api(monkeypatch) -> None:
    from readio.config import ReaderSettings, ReadioConfig
    from readio.document import document_from_text
    from readio.engines import registry
    from readio.engines.api_probe import EngineApiProbe
    from readio.engines.base import EngineCapabilities
    from readio.plan import (
        InputRequest,
        OutputRequest,
        PlanRequest,
        SynthesisRequest,
        resolve_execution_v2,
    )

    class IncompatibleAdapter:
        id = "incompatible"
        package_name = "test-engine"

        probe_calls = 0

        def probe_api(self):
            self.probe_calls += 1
            return EngineApiProbe(
                engine=self.id,
                package=self.package_name,
                compatible=False,
                status="api_incompatible",
                distribution_version="0.1.0",
                expected_api_version=1,
                missing_symbols=("SynthesisRequest",),
            )

        def version(self):
            return "0.1.0"

        def capabilities(self):
            return EngineCapabilities(id=self.id)

        def resolve(self, _request):
            raise AssertionError("incompatible adapter must not resolve requests")

    adapter = IncompatibleAdapter()
    monkeypatch.setattr(registry, "get_engine", lambda _engine: adapter)
    request = PlanRequest(
        operation="render",
        input=InputRequest(document=document_from_text("Plan with an incompatible engine.")),
        synthesis=SynthesisRequest(engine="incompatible"),
        output=OutputRequest(),
    )

    resolved = resolve_execution_v2(
        ReadioConfig(reader=ReaderSettings(engine="incompatible")),
        request,
    )

    assert resolved.semantic is not None
    assert resolved.plan.render is None
    assert not resolved.plan.ok
    assert any(item.code == "engine_api_incompatible" for item in resolved.plan.diagnostics)
    assert adapter.probe_calls == 1
    diagnostic = next(
        item for item in resolved.plan.diagnostics if item.code == "engine_api_incompatible"
    )
    assert "missing public symbols SynthesisRequest" in diagnostic.message


def test_engine_probe_statuses_map_to_distinct_plan_diagnostics() -> None:
    from readio.engines.api_probe import EngineApiProbe
    from readio.plan import _engine_api_probe_diagnostics

    probes = (
        (
            EngineApiProbe(
                engine="kokoro",
                package="pykokoro",
                compatible=False,
                status="package_missing",
            ),
            "engine_package_missing",
        ),
        (
            EngineApiProbe(
                engine="supertonic",
                package="supertonicsynth",
                compatible=False,
                status="api_version_incompatible",
                distribution_version="0.2.0",
                api_version=2,
                expected_api_version=1,
            ),
            "engine_api_version_incompatible",
        ),
        (
            EngineApiProbe(
                engine="kokoro",
                package="pykokoro",
                compatible=False,
                status="api_probe_failed",
                distribution_version="0.10.3",
                failed_stage="symbol_resolution",
                failed_symbol="KokoroSynthesizer",
                error_type="ImportError",
                error_message="missing transitive dependency",
            ),
            "engine_api_probe_failed",
        ),
    )
    for probe, code in probes:
        diagnostics = _engine_api_probe_diagnostics(probe)
        assert len(diagnostics) == 1
        assert diagnostics[0].code == code

    mismatch = EngineApiProbe(
        engine="kokoro",
        package="pykokoro",
        compatible=True,
        status="ready",
        distribution_version="0.10.4",
        module_version="0.10.3",
        module_path="/checkout/pykokoro/__init__.py",
    )
    warning = _engine_api_probe_diagnostics(mismatch)
    assert len(warning) == 1
    assert warning[0].code == "engine_module_version_mismatch"
    assert warning[0].severity == "warning"
    assert "/checkout/pykokoro/__init__.py" in warning[0].message


def test_inflect_engine_probe_install_hint_uses_optional_extra() -> None:
    from readio.engines.api_probe import EngineApiProbe
    from readio.plan import _engine_api_probe_diagnostics

    diagnostic = _engine_api_probe_diagnostics(
        EngineApiProbe(
            engine="inflect",
            package="inflectsynth",
            compatible=False,
            status="package_missing",
        )
    )[0]
    assert diagnostic.code == "engine_package_missing"
    assert "InflectSynth is not installed" in diagnostic.message
    assert "pip install readio[inflect]" in diagnostic.message


def test_pocket_voice_file_is_hashed_into_the_resolved_render_target(monkeypatch, tmp_path) -> None:
    import hashlib

    from readio.config import ReaderSettings, ReadioConfig
    from readio.document import document_from_text
    from readio.engines import registry
    from readio.engines.api_probe import EngineApiProbe
    from readio.engines.base import EngineCapabilities, EngineSelection
    from readio.plan import (
        InputRequest,
        OutputRequest,
        PlanRequest,
        SynthesisRequest,
        resolve_execution_v2,
    )

    class PocketAdapter:
        id = "pocket"
        package_name = "pocketsynth"

        def probe_api(self):
            return EngineApiProbe(
                engine=self.id,
                package=self.package_name,
                compatible=True,
                status="ready",
                distribution_version="0.1.0",
                expected_api_version=1,
            )

        def version(self):
            return "0.1.0"

        def capabilities(self):
            return EngineCapabilities(
                id=self.id,
                voice_binding_namespace=self.id,
                supports_reference_voice=True,
                option_names=frozenset({"voice_source"}),
            )

        def resolve(self, request):
            source = dict(request.engine_options["voice_source"])
            if source.get("kind") == "managed_reference":
                source.update(
                    {
                        "sha256": "a" * 64,
                        "source_revision": "catalog-rev-3",
                        "source_repository": "kyutai/voices",
                        "source_path": "alba/casual.wav",
                        "license": "cc-by-4.0",
                        "dataset": "alba",
                        "variant": "casual",
                    }
                )
            return (
                EngineSelection(
                    engine=self.id,
                    target_id=request.target_id or "bundle",
                    language=request.language or "en-us",
                    metadata={"voice_source": source},
                ),
                (),
            )

        def validate_selection(self, _selection):
            return ()

        def target_metadata(self, selection):
            return selection.metadata

    monkeypatch.setattr(registry, "get_engine", lambda _engine: PocketAdapter())
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"reference audio content")
    expected_digest = hashlib.sha256(reference.read_bytes()).hexdigest()
    request = PlanRequest(
        operation="render",
        input=InputRequest(document=document_from_text("Use a reference voice.")),
        synthesis=SynthesisRequest(engine="pocket", voice_file=reference),
        output=OutputRequest(),
    )

    resolved = resolve_execution_v2(
        ReadioConfig(reader=ReaderSettings(engine="pocket")),
        request,
    )

    assert resolved.plan.ok
    target_voice = resolved.plan.render.default_target.voice
    assert target_voice.kind == "reference"
    assert target_voice.value == str(reference.resolve())
    assert target_voice.sha256 == expected_digest
    prompt_ref = "kyutai-tts-voices:alba/casual"
    managed_request = replace(
        request,
        synthesis=SynthesisRequest(engine="pocket", voice_prompt=prompt_ref),
    )
    managed = resolve_execution_v2(
        ReadioConfig(reader=ReaderSettings(engine="pocket")),
        managed_request,
    )
    managed_voice = managed.plan.render.default_target.voice
    assert managed.plan.ok
    assert managed_voice.kind == "managed_reference"
    assert managed_voice.value == prompt_ref
    assert managed_voice.sha256 == "a" * 64
    assert managed_voice.source_revision == "catalog-rev-3"
    assert managed_voice.source_repository == "kyutai/voices"
    assert managed_voice.source_path == "alba/casual.wav"
    assert managed_voice.license == "cc-by-4.0"
    assert managed_voice.dataset == "alba"
    assert managed_voice.variant == "casual"
    reloaded = RenderTargetV2.from_dict(managed.plan.render.default_target.to_dict())
    assert reloaded.voice == managed_voice
