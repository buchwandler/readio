"""Tests for the engine-neutral readio.plan.v2 schema."""

from __future__ import annotations

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
    assert ref.schema_version == CURRENT_SCHEMA_VERSION == 3
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
        synthesis=SynthesisRequest(engine="missing-engine"),
        output=OutputRequest(),
    )

    resolved = resolve_execution_v2(
        ReadioConfig(reader=ReaderSettings(engine="missing-engine")),
        request,
    )

    assert resolved.semantic is not None
    assert resolved.plan.semantic_plan.plan_id == resolved.semantic.plan_id
    assert resolved.plan.render is None
    assert any(item.code == "engine_unavailable" for item in resolved.plan.diagnostics)


def test_semantic_plan_compiles_but_skips_incompatible_engine_api(monkeypatch) -> None:
    from readio.config import ReaderSettings, ReadioConfig
    from readio.document import document_from_text
    from readio.engines import registry
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

        def compatible_api(self):
            return False

        def version(self):
            return "0.1.0"

        def capabilities(self):
            return EngineCapabilities(id=self.id)

        def resolve(self, _request):
            raise AssertionError("incompatible adapter must not resolve requests")

    monkeypatch.setattr(registry, "get_engine", lambda _engine: IncompatibleAdapter())
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


def test_pocket_voice_file_is_hashed_into_the_resolved_render_target(monkeypatch, tmp_path) -> None:
    import hashlib

    from readio.config import ReaderSettings, ReadioConfig
    from readio.document import document_from_text
    from readio.engines import registry
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

        def compatible_api(self):
            return True

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
            return (
                EngineSelection(
                    engine=self.id,
                    target_id=request.target_id or "bundle",
                    language=request.language or "en-us",
                    metadata={"voice_source": request.engine_options["voice_source"]},
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
