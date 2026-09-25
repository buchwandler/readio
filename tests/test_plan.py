"""Tests for the request-centric plan-v2 contract."""

from __future__ import annotations

from pathlib import Path

from readio.config import ReadioConfig, with_overrides
from readio.document import InputDocument
from readio.plan import (
    DIAG_OUTPUT_FORMAT_CONFLICT,
    InputRequest,
    OutputRequest,
    PlanRequest,
    SynthesisRequest,
    format_plan_human,
    resolve_plan,
)


def _config(**overrides) -> ReadioConfig:
    config = ReadioConfig()
    return with_overrides(config, **overrides) if overrides else config


def _request(
    *,
    text: str = "Hello world.",
    format: str = "text",
    synthesis: SynthesisRequest | None = None,
    output: OutputRequest | None = None,
    voice_bindings: dict[str, str] | None = None,
) -> PlanRequest:
    return PlanRequest(
        operation="render",
        input=InputRequest(document=InputDocument(text, None, format)),
        synthesis=synthesis or SynthesisRequest(),
        output=output or OutputRequest(),
        voice_bindings=voice_bindings or {},
    )


def test_plan_represents_semantics_render_target_environment_and_output():
    plan = resolve_plan(_config(), _request())

    assert plan.ok
    assert plan.schema == "readio.plan.v2"
    assert plan.input.source_kind == "stdin"
    assert plan.input.format == "text"
    assert plan.semantic_plan.schema_version == 3
    assert plan.planning.language == "en-us"
    assert plan.render.engine == "pykokoro"
    assert plan.render.default_target.id
    assert plan.environment.packages["readio"]
    assert plan.environment.packages["utterplan"]
    assert plan.output.format == "wav"
    assert plan.output.mode == "file"


def test_language_override_is_recorded_in_plan_decisions():
    plan = resolve_plan(_config(), _request(synthesis=SynthesisRequest(language="de")))

    assert plan.planning.language == "de"
    decision = next(item for item in plan.decisions if item.field == "synthesis.language")
    assert decision.value == "de"
    assert decision.origin == "cli"


def test_lexicon_order_and_explicit_disable_are_render_options():
    plan = resolve_plan(_config(), _request(synthesis=SynthesisRequest(lexicons=("gold", "crane"))))
    disabled = resolve_plan(_config(), _request(synthesis=SynthesisRequest(clear_lexicons=True)))

    assert plan.render.options["lexicons"] == ("gold", "crane")
    assert disabled.render.options["lexicons"] == ()
    assert disabled.to_dict()["render"]["options"]["lexicons"] == ()


def test_reader_policy_overrides_are_in_planning_and_render_target():
    plan = resolve_plan(
        _config(pause_mode="manual"),
        _request(
            synthesis=SynthesisRequest(
                speed=1.5,
                pause_mode="tts",
                spacy="lg",
                short_sentence="wrap",
                voice_level="calibrated",
            )
        ),
    )

    assert plan.planning.pause_mode == "tts"
    assert plan.planning.spacy == "lg"
    assert plan.render.rate == 1.0
    assert plan.render.options["speed"] == 1.5
    assert plan.render.options["voice_level"] == "calibrated"
    assert plan.render.options["short_sentence"] == "wrap"
    assert (
        next(item for item in plan.decisions if item.field == "synthesis.pause_mode").origin
        == "cli"
    )
    assert (
        next(item for item in plan.decisions if item.field == "synthesis.voice_level").origin
        == "cli"
    )
    assert next(item for item in plan.decisions if item.field == "synthesis.speed").origin == "cli"


def test_output_format_path_conflict_is_reported():
    plan = resolve_plan(
        _config(),
        _request(
            output=OutputRequest(
                requested_format="mp3",
                requested_path=Path("/tmp/episode.wav"),
            )
        ),
    )

    assert not plan.ok
    assert any(item.code == DIAG_OUTPUT_FORMAT_CONFLICT for item in plan.diagnostics)


def test_output_path_origin_and_force_are_serialized(tmp_path: Path):
    path = tmp_path / "episode"
    plan = resolve_plan(
        _config(),
        _request(output=OutputRequest(requested_path=path, requested_format="mp3", force=True)),
    )

    assert plan.output.path == tmp_path / "episode.mp3"
    assert plan.output.path_origin == "explicit"
    assert plan.output.force is True
    assert plan.to_dict()["output"]["force"] is True


def test_unresolved_ssmd_role_produces_a_plan_diagnostic():
    text = '---\nssmd_version: "0.9"\n---\n:::{voice="nonexistent_role"}\nHello world.\n:::\n'
    plan = resolve_plan(_config(), _request(text=text, format="ssmd"))

    assert not plan.ok
    assert any(item.code == "ssmd_unresolved_voice" for item in plan.diagnostics)


def test_plan_serialization_and_human_report_use_v2_sections():
    plan = resolve_plan(_config(), _request())
    data = plan.to_dict()
    text = format_plan_human(plan)

    assert data["schema"] == "readio.plan.v2"
    assert "planning" in data and "render" in data and "environment" in data
    assert "synthesis" not in data
    assert all(
        section in text for section in ("Input", "Planning", "Semantic plan", "Render", "Output")
    )
    assert "Plan is executable" in text
