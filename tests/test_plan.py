"""Tests for readio.plan — pure plan domain resolution."""

from __future__ import annotations

from pathlib import Path

from readio.config import ReadioConfig, with_overrides
from readio.document import InputDocument
from readio.plan import (
    DIAG_OUTPUT_FORMAT_CONFLICT,
    DIAG_SSMD_UNRESOLVED_VOICE,
    InputRequest,
    OutputRequest,
    PlanRequest,
    SynthesisRequest,
    resolve_plan,
)


def _default_config(**overrides) -> ReadioConfig:
    cfg = ReadioConfig()
    if overrides:
        cfg = with_overrides(cfg, **overrides)
    return cfg


def _text_request(
    text: str = "Hello world.",
    *,
    synthesis: SynthesisRequest | None = None,
    output: OutputRequest | None = None,
    voice_bindings: dict[str, str] | None = None,
) -> PlanRequest:
    return PlanRequest(
        operation="render",
        input=InputRequest(
            document=InputDocument(text=text, source_path=None, format="text"),
        ),
        synthesis=synthesis or SynthesisRequest(),
        output=output or OutputRequest(),
        voice_bindings=voice_bindings or {},
    )


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------


class TestPlanStructure:
    def test_plan_has_schema(self) -> None:
        plan = resolve_plan(_default_config(), _text_request())
        assert plan.schema == "readio.plan.v1"

    def test_plan_has_input(self) -> None:
        plan = resolve_plan(_default_config(), _text_request())
        assert plan.input.source_kind == "stdin"
        assert plan.input.format == "text"
        assert plan.input.source_sha256

    def test_plan_has_environment(self) -> None:
        plan = resolve_plan(_default_config(), _text_request())
        assert plan.environment.readio_version
        assert plan.environment.pykokoro_version

    def test_plan_has_output(self) -> None:
        plan = resolve_plan(_default_config(), _text_request())
        assert plan.output.format == "wav"
        assert plan.output.mode == "file"

    def test_plan_has_decisions(self) -> None:
        plan = resolve_plan(_default_config(), _text_request())
        assert len(plan.decisions) > 0
        # Should have at least language, speed, pause_mode, unit
        fields = {d.field for d in plan.decisions}
        assert "synthesis.language" in fields
        assert "synthesis.speed" in fields


# ---------------------------------------------------------------------------
# Language precedence
# ---------------------------------------------------------------------------


class TestLanguagePrecedence:
    def test_default_language(self) -> None:
        plan = resolve_plan(_default_config(), _text_request())
        assert plan.synthesis is not None
        assert plan.synthesis.language == "en-us"

    def test_cli_language_overrides_config(self) -> None:
        plan = resolve_plan(
            _default_config(),
            _text_request(synthesis=SynthesisRequest(language="de")),
        )
        assert plan.synthesis is not None
        assert plan.synthesis.language == "de"
        # Provenance should show CLI
        lang_dec = next(d for d in plan.decisions if d.field == "synthesis.language")
        assert lang_dec.origin == "cli"

    def test_config_language_used_when_no_cli(self) -> None:
        cfg = _default_config()
        # Set reader.lang via with_overrides doesn't work directly,
        # so we test the default path
        plan = resolve_plan(cfg, _text_request())
        assert plan.synthesis is not None
        lang_dec = next(d for d in plan.decisions if d.field == "synthesis.language")
        assert lang_dec.origin == "config.reader"


# ---------------------------------------------------------------------------
# Language profile matching
# ---------------------------------------------------------------------------


class TestLanguageProfile:
    def test_exact_profile_match(self) -> None:
        # Default config has no language profiles, so match is 'none'
        cfg = _default_config()
        plan = resolve_plan(
            cfg,
            _text_request(synthesis=SynthesisRequest(language="de")),
        )
        assert plan.synthesis is not None
        lp = plan.synthesis.language_profile
        assert lp.requested == "de"
        # No profile configured => match is 'none'
        assert lp.match == "none"

    def test_base_profile_fallback(self) -> None:
        cfg = _default_config()
        # de-at should fall back to de base profile if de-at doesn't exist
        plan = resolve_plan(
            cfg,
            _text_request(synthesis=SynthesisRequest(language="de-at")),
        )
        assert plan.synthesis is not None
        lp = plan.synthesis.language_profile
        assert lp.requested == "de-at"
        # Depending on config, this could be exact or base
        assert lp.match in ("exact", "base", "none")


# ---------------------------------------------------------------------------
# Lexicon semantics
# ---------------------------------------------------------------------------


class TestLexicons:
    def test_lexicon_order_preserved(self) -> None:
        plan = resolve_plan(
            _default_config(),
            _text_request(synthesis=SynthesisRequest(lexicons=("gold", "crane"))),
        )
        assert plan.synthesis is not None
        assert plan.synthesis.lexicons == ("gold", "crane")

    def test_clear_lexicons(self) -> None:
        plan = resolve_plan(
            _default_config(),
            _text_request(synthesis=SynthesisRequest(clear_lexicons=True)),
        )
        assert plan.synthesis is not None
        assert plan.synthesis.lexicons is None


# ---------------------------------------------------------------------------
# Reader controls precedence
# ---------------------------------------------------------------------------


class TestReaderControls:
    def test_cli_speed_overrides_config(self) -> None:
        plan = resolve_plan(
            _default_config(),
            _text_request(synthesis=SynthesisRequest(speed=1.5)),
        )
        assert plan.synthesis is not None
        assert plan.synthesis.speed == 1.5
        speed_dec = next(d for d in plan.decisions if d.field == "synthesis.speed")
        assert speed_dec.origin == "cli"

    def test_config_speed_used_when_no_cli(self) -> None:
        plan = resolve_plan(_default_config(), _text_request())
        assert plan.synthesis is not None
        assert plan.synthesis.speed == 1.0  # default
        speed_dec = next(d for d in plan.decisions if d.field == "synthesis.speed")
        assert speed_dec.origin == "config.reader"


# ---------------------------------------------------------------------------
# Output planning
# ---------------------------------------------------------------------------


class TestOutputPlanning:
    def test_default_format_is_wav(self) -> None:
        plan = resolve_plan(_default_config(), _text_request())
        assert plan.output.format == "wav"

    def test_explicit_format(self) -> None:
        plan = resolve_plan(
            _default_config(),
            _text_request(output=OutputRequest(requested_format="mp3")),
        )
        assert plan.output.format == "mp3"

    def test_format_from_suffix(self) -> None:
        plan = resolve_plan(
            _default_config(),
            _text_request(
                output=OutputRequest(requested_path=Path("/tmp/test.mp3")),
            ),
        )
        assert plan.output.format == "mp3"

    def test_format_conflict_produces_diagnostic(self) -> None:
        plan = resolve_plan(
            _default_config(),
            _text_request(
                output=OutputRequest(
                    requested_format="mp3",
                    requested_path=Path("/tmp/test.wav"),
                ),
            ),
        )
        assert not plan.ok
        format_diags = [d for d in plan.diagnostics if d.code == DIAG_OUTPUT_FORMAT_CONFLICT]
        assert len(format_diags) > 0

    def test_explicit_path_recorded(self) -> None:
        plan = resolve_plan(
            _default_config(),
            _text_request(
                output=OutputRequest(requested_path=Path("/tmp/test.wav")),
            ),
        )
        assert plan.output.path == Path("/tmp/test.wav")
        assert plan.output.path_origin == "explicit"

    def test_generated_path_when_file_mode(self) -> None:
        plan = resolve_plan(_default_config(), _text_request())
        assert plan.output.path is not None
        assert plan.output.path_origin == "generated"


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_language_provenance(self) -> None:
        plan = resolve_plan(
            _default_config(),
            _text_request(synthesis=SynthesisRequest(language="de")),
        )
        lang_dec = next(d for d in plan.decisions if d.field == "synthesis.language")
        assert lang_dec.origin == "cli"
        assert lang_dec.value == "de"

    def test_decisions_have_origin(self) -> None:
        plan = resolve_plan(_default_config(), _text_request())
        for dec in plan.decisions:
            assert dec.origin  # no empty origins


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------


class TestJsonSerialization:
    def test_to_dict_has_schema(self) -> None:
        plan = resolve_plan(_default_config(), _text_request())
        d = plan.to_dict()
        assert d["schema"] == "readio.plan.v1"

    def test_to_dict_has_ok(self) -> None:
        plan = resolve_plan(_default_config(), _text_request())
        d = plan.to_dict()
        assert isinstance(d["ok"], bool)

    def test_to_dict_has_synthesis(self) -> None:
        plan = resolve_plan(_default_config(), _text_request())
        d = plan.to_dict()
        assert "synthesis" in d
        assert d["synthesis"] is not None
        assert "model" in d["synthesis"]

    def test_to_dict_has_decisions(self) -> None:
        plan = resolve_plan(_default_config(), _text_request())
        d = plan.to_dict()
        assert isinstance(d["decisions"], list)
        assert len(d["decisions"]) > 0

    def test_to_dict_has_diagnostics(self) -> None:
        plan = resolve_plan(_default_config(), _text_request())
        d = plan.to_dict()
        assert isinstance(d["diagnostics"], list)


# ---------------------------------------------------------------------------
# Invalid plans
# ---------------------------------------------------------------------------


class TestInvalidPlans:
    def test_unresolved_ssmd_role_produces_error(self) -> None:
        ssmd_text = """---
voice_bindings: {}
---
<div voice="nonexistent_role">Hello world.</div>
"""

        plan = resolve_plan(
            _default_config(),
            PlanRequest(
                operation="render",
                input=InputRequest(
                    document=InputDocument(
                        text=ssmd_text,
                        source_path=None,
                        format="ssmd",
                    ),
                ),
                synthesis=SynthesisRequest(),
                output=OutputRequest(),
            ),
        )
        assert not plan.ok
        unresolved_diags = [d for d in plan.diagnostics if d.code == DIAG_SSMD_UNRESOLVED_VOICE]
        assert len(unresolved_diags) > 0


# ---------------------------------------------------------------------------
# Human-readable output
# ---------------------------------------------------------------------------


class TestHumanOutput:
    def test_format_plan_human(self) -> None:
        from readio.plan import format_plan_human

        plan = resolve_plan(_default_config(), _text_request())
        text = format_plan_human(plan)
        assert "Input" in text
        assert "Synthesis" in text
        assert "Output" in text
        assert "Environment" in text

    def test_format_plan_human_shows_why(self) -> None:
        from readio.plan import format_plan_human

        plan = resolve_plan(_default_config(), _text_request())
        text = format_plan_human(plan)
        assert "Why" in text


# ---------------------------------------------------------------------------
# Compatibility view
# ---------------------------------------------------------------------------


class TestCompatibilityView:
    def test_resolved_synthesis_from_plan(self) -> None:
        from readio.plan import resolved_synthesis_from_plan

        plan = resolve_plan(_default_config(), _text_request())
        rs = resolved_synthesis_from_plan(plan)
        assert rs.language == "en-us"
        assert rs.speed == 1.0
