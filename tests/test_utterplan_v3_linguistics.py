from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from utterplan import UtterancePlan

from readio.config import ReaderSettings, ReadioConfig
from readio.document import document_from_text
from readio.plan import SemanticPlanRef
from readio.planning.compiler import compile_semantic_plan
from readio.planning.policy import PlanningPolicy, _linguistics_from_spacy_policy
from readio.project import init_project
from readio.stages.planning import (
    PlanSchemaMismatchError,
    load_utterplan_v3,
    plan_project,
    semantic_status,
)


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
def test_spacy_policy_maps_to_exact_utterplan_config(
    policy: str, use_spacy: bool, model_size: str | None, require_spacy: bool
) -> None:
    assert _linguistics_from_spacy_policy(policy) == __import__("utterplan").LinguisticsConfig(
        use_spacy=use_spacy,
        spacy_model_size=model_size,
        require_spacy=require_spacy,
    )


def test_spacy_policy_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="unsupported Readio spaCy policy"):
        _linguistics_from_spacy_policy("xx")


def test_plan_persists_v3_tokens_and_linguistic_runs() -> None:
    compiled = compile_semantic_plan(
        document_from_text("Hello world."),
        planning=PlanningPolicy(spacy_policy="off"),
    )
    data = json.loads(compiled.plan.to_json())

    assert compiled.plan.schema_version == 3
    assert data["schema_version"] == 3
    assert data["linguistic_runs"]
    assert data["tokens"]
    assert all("token_indices" in segment for segment in data["segments"])
    assert all("content_hash" in unit for unit in data["units"])


def test_fake_spacy_annotations_and_provenance_are_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from utterplan.linguistics import LinguisticAnalysis, LinguisticResourcePool
    from utterplan.model import TokenAnnotation

    class FakePool(LinguisticResourcePool):
        def analyze(self, text, run, config):
            tokens = []
            words = text.split()
            for index, word in enumerate(words):
                start = text.index(word, sum(len(item.text) + 1 for item in tokens))
                is_adjective = (
                    index > 0
                    and word.lower().startswith("live")
                    and words[index - 1].lower() == "a"
                )
                tokens.append(
                    TokenAnnotation(
                        spoken_start=start,
                        spoken_end=start + len(word),
                        text=word,
                        pos="ADJ" if is_adjective else "VERB",
                        tag="JJ" if is_adjective else "VBP",
                        lemma=word.lower().rstrip("."),
                        language=run.language,
                        id=f"token-{index}",
                        morph="Degree=Pos" if is_adjective else "Tense=Pres",
                    )
                )
            return LinguisticAnalysis(
                language=run.language,
                text=text,
                tokens=tuple(tokens),
                provider="spacy",
                model_name="en_core_web_sm",
                provider_version="3.8.0",
                model_version="3.8.0",
            )

    monkeypatch.setattr("utterplan.planner.LinguisticResourcePool", FakePool)
    compiled = compile_semantic_plan(
        document_from_text("I live here.\n\na live show."),
        planning=PlanningPolicy(unit="sentence", spacy_policy="auto"),
    )
    live = [token for token in compiled.plan.tokens if token.text.lower() == "live"]

    assert [(token.pos, token.tag, token.morph) for token in live] == [
        ("VERB", "VBP", "Tense=Pres"),
        ("ADJ", "JJ", "Degree=Pos"),
    ]
    assert compiled.plan.linguistic_runs[0].provider == "spacy"
    assert compiled.plan.linguistic_runs[0].model == "en_core_web_sm"
    assert compiled.plan.linguistic_runs[0].provider_version == "3.8.0"


def test_schema_v3_loader_round_trip(tmp_path: Path) -> None:
    compiled = compile_semantic_plan(
        document_from_text("Hello."), planning=PlanningPolicy(spacy_policy="off")
    )
    path = tmp_path / "plan.json"
    path.write_text(compiled.plan.to_json(), encoding="utf-8")
    loaded = load_utterplan_v3(path)

    assert loaded.plan_id == compiled.plan.plan_id
    assert loaded.tokens == compiled.plan.tokens
    assert loaded.linguistic_runs == compiled.plan.linguistic_runs


@pytest.mark.parametrize(
    ("stored", "has_schema_version"),
    [(1, True), (2, True), (4, True), (None, True), (3.0, True), (True, True), (None, False)],
)
def test_schema_v3_loader_rejects_unsupported_versions_before_upstream_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stored: object,
    has_schema_version: bool,
) -> None:
    compiled = compile_semantic_plan(
        document_from_text("Hello."), planning=PlanningPolicy(spacy_policy="off")
    )
    data = json.loads(compiled.plan.to_json())
    if has_schema_version:
        data["schema_version"] = stored
    else:
        data.pop("schema_version")
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    def unexpected_load(cls: type[UtterancePlan], payload: dict[str, object]) -> UtterancePlan:
        pytest.fail("UtterancePlan.from_dict must not run for unsupported Readio schemas")

    monkeypatch.setattr(UtterancePlan, "from_dict", classmethod(unexpected_load))
    with pytest.raises(PlanSchemaMismatchError) as error:
        load_utterplan_v3(path)
    assert error.value.stored == stored


@pytest.mark.parametrize("stored", [1, 2])
def test_schema_mismatch_is_stale_and_actionable(tmp_path: Path, stored: int) -> None:
    source = tmp_path / "episode.txt"
    source.write_text("Hello.", encoding="utf-8")
    project = init_project(source, tmp_path / "episode.readio")
    plan_project(project, ReadioConfig(reader=ReaderSettings(spacy="off")))
    path = project.root / "plan" / "document.utterplan.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema_version"] = stored
    path.write_text(json.dumps(data), encoding="utf-8")
    index = json.loads(project.paths["plan_index"].read_text(encoding="utf-8"))
    index["scopes"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    project.paths["plan_index"].write_text(json.dumps(index), encoding="utf-8")

    status = semantic_status(project)[-1]
    assert status["reason"] == "plan.artifact.schema_mismatch"
    assert status["stored"] == stored
    assert status["required"] == 3
    assert status["action"] == "run readio plan"


def test_linguistic_annotation_changes_only_affected_unit_hash() -> None:
    from utterplan.units import make_units

    plan = compile_semantic_plan(
        document_from_text("I live here.\n\na live show."),
        planning=PlanningPolicy(unit="sentence", spacy_policy="off"),
    ).plan
    live_index = next(index for index, token in enumerate(plan.tokens) if token.text == "live")
    changed_token = replace(plan.tokens[live_index], pos="VERB", tag="VBP", morph="Tense=Pres")
    tokens = (*plan.tokens[:live_index], changed_token, *plan.tokens[live_index + 1 :])
    changed_units = make_units(plan.segments, plan.markers, tokens, "sentence")
    changed = replace(plan, tokens=tokens, units=changed_units).with_identity()

    assert changed.units[0].content_hash != plan.units[0].content_hash
    assert changed.units[1].content_hash == plan.units[1].content_hash
    assert SemanticPlanRef().schema_version == 3


def test_plan_render_sessions_forward_acoustic_options_only() -> None:
    from readio.engines.pipersynth import PiperSynthEngineSession

    class Pipeline:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def prepare_plan(self, plan, **options):
            self.calls.append(dict(options))
            return object()

        def to_audio_job(self, plan, **options):
            self.calls.append(dict(options))
            return object()

    pipeline = Pipeline()
    session = PiperSynthEngineSession(pipeline)
    options = {
        "spacy": "lg",
        "pause_mode": "auto",
        "length_scale": 0.8,
        "noise_scale": 0.5,
    }
    session.prepare_plan(object(), options=options)
    session.to_audio_job(object(), options=options)

    assert pipeline.calls == [
        {"length_scale": 0.8, "noise_scale": 0.5},
        {"length_scale": 0.8, "noise_scale": 0.5},
    ]
