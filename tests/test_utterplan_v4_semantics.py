from __future__ import annotations

import pytest
from utterplan import (
    CURRENT_SCHEMA_VERSION,
    LinguisticsConfig,
    PlanSegment,
    SemanticBoundary,
    SSMDConfig,
    UtterancePlan,
    compile_document,
)

from readio.document import document_from_text
from readio.plan import SemanticPlanRef
from readio.planning.policy import PlanningPolicy, _linguistics_from_spacy_policy
from readio.planning.semantic import SUPPORTED_UTTERPLAN_SCHEMA_VERSION, SemanticPlanningService


@pytest.mark.parametrize(
    ("policy", "use_spacy", "model_size", "require_spacy"),
    [
        ("auto", True, None, False),
        ("off", False, None, False),
        ("sm", True, "sm", True),
        ("md", True, "md", True),
        ("lg", True, "lg", True),
        ("trf", True, "trf", True),
    ],
)
def test_spacy_policy_maps_to_public_utterplan_config(
    policy: str, use_spacy: bool, model_size: str | None, require_spacy: bool
) -> None:
    assert _linguistics_from_spacy_policy(policy) == LinguisticsConfig(
        use_spacy=use_spacy,
        spacy_model_size=model_size,
        require_spacy=require_spacy,
    )


def test_spacy_policy_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="unsupported Readio spaCy policy"):
        _linguistics_from_spacy_policy("xx")


def test_utterplan_v4_public_semantic_contract() -> None:
    assert CURRENT_SCHEMA_VERSION == 4
    assert SUPPORTED_UTTERPLAN_SCHEMA_VERSION == 4
    assert SemanticPlanRef().schema_version == 4
    assert SemanticBoundary.__name__ == "SemanticBoundary"
    assert callable(UtterancePlan.semantic_boundaries_for_segment)
    assert callable(UtterancePlan.semantic_boundaries_in_range)


def test_readio_compilation_uses_public_compile_document_api() -> None:
    policy = PlanningPolicy(document_format="plain", spacy_policy="off")
    document = document_from_text("Hello world.", input_format="text")

    plan = SemanticPlanningService().compile(document.text, policy)
    direct = compile_document(
        document.text,
        input_format=policy.document_format,
        config=policy.to_planner_config(),
        trace=False,
    ).plan

    assert plan.schema_version == 4
    assert plan.texts.spoken == direct.texts.spoken
    assert plan.segments
    assert all(isinstance(segment, PlanSegment) for segment in plan.segments)
    assert all(
        isinstance(plan.semantic_boundaries_for_segment(segment), tuple)
        for segment in plan.segments
    )


def test_ssmd_policy_constructs_and_compiles_with_utterplan_v4() -> None:
    policy = PlanningPolicy(
        document_format="ssmd",
        spacy_policy="off",
        ssmd=SSMDConfig(),
    )
    document = document_from_text("Hello from SSMD.", input_format="ssmd")

    plan = SemanticPlanningService().compile(document.text, policy)

    assert plan.schema_version == 4
    assert plan.texts.spoken


def test_serialize_utterplan_uses_canonical_toml_and_current_loader(tmp_path) -> None:
    import hashlib

    from readio.planning.compiler import compile_semantic_plan, load_current_utterplan

    document = document_from_text("A canonical plan.", input_format="text")
    compiled = compile_semantic_plan(
        document,
        planning=PlanningPolicy(document_format="plain", spacy_policy="off"),
    )
    serialized = compiled.plan.to_toml().encode("utf-8")
    path = tmp_path / "document.utterplan.toml"
    path.write_bytes(serialized)

    loaded = load_current_utterplan(path)

    assert compiled.serialized == serialized
    assert compiled.sha256 == hashlib.sha256(serialized).hexdigest()
    assert loaded.schema_version == 4
    assert loaded.plan_id == compiled.plan_id


def test_current_loader_rejects_json_without_migration(tmp_path) -> None:
    from readio.planning.compiler import LegacyPlanArtifactError, load_current_utterplan

    legacy_path = tmp_path / "old.utterplan.json"
    legacy_path.write_text('{"format":"utterplan","schema_version":3}', encoding="utf-8")

    with pytest.raises(LegacyPlanArtifactError):
        load_current_utterplan(legacy_path)
