"""Focused validation tests for ReadioPlan capability checks and provenance.

Covers the review items:

- model/language compatibility (model_language_incompatible)
- runtime availability (model_runtime_unavailable)
- concrete-field invariant (synthesis_incomplete)
- provenance completeness (discovered source, profile allow_experimental,
  systematic SSMD binding decisions)
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from readio import plan as plan_module
from readio.config import LanguageSettings, ReadioConfig
from readio.document import InputDocument
from readio.models import ModelInfo
from readio.plan import (
    InputRequest,
    OutputRequest,
    PlanRequest,
    SynthesisRequest,
    resolve_plan,
)


def _text_request(
    synthesis: SynthesisRequest | None = None,
    *,
    source_kind: str | None = None,
) -> PlanRequest:
    return PlanRequest(
        operation="render",
        input=InputRequest(
            document=InputDocument(text="Hello world.", source_path=None, format="text"),
            source_kind=source_kind,  # type: ignore[arg-type]
        ),
        synthesis=synthesis or SynthesisRequest(),
        output=OutputRequest(),
    )


def _diagnostic_codes(result) -> set[str]:
    return {d.code for d in result.diagnostics}


def _fake_model(**overrides) -> ModelInfo:
    values: dict[str, object] = {
        "id": "fake-en",
        "source": "github",
        "languages": ("en",),
        "voices": ("af_sarah", "am_michael"),
        "default_voice": "af_sarah",
        "qualities": ("fp32", "int8"),
        "g2p_backend": "misaki",
        "lexicons": None,
        "frontend": "kokoro",
        "status": "ready",
        "experimental": False,
        "runtime_available": True,
        "redistribution_allowed": True,
    }
    values.update(overrides)
    return ModelInfo(**values)


class TestLanguageCompatibility:
    def test_plan_rejects_model_language_incompatible(self) -> None:
        # de-thorsten declares only German; the request language is en-us.
        result = resolve_plan(
            ReadioConfig(),
            _text_request(SynthesisRequest(model="de-thorsten")),
        )
        assert not result.ok
        assert "model_language_incompatible" in _diagnostic_codes(result)
        diag = next(d for d in result.diagnostics if d.code == "model_language_incompatible")
        assert "de" in diag.message

    def test_plan_accepts_matching_language(self) -> None:
        result = resolve_plan(
            ReadioConfig(),
            _text_request(SynthesisRequest(model="de-thorsten", language="de")),
        )
        assert result.ok
        assert result.synthesis is not None
        assert result.synthesis.model.id == "de-thorsten"


class TestRuntimeAvailability:
    def test_plan_rejects_runtime_unavailable_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            plan_module,
            "get_model_info",
            lambda *a, **k: (
                _fake_model(runtime_available=False, status="registry-unavailable"),
                None,
            ),
        )
        result = resolve_plan(
            ReadioConfig(),
            _text_request(SynthesisRequest(model="fake-en")),
        )
        assert not result.ok
        assert "model_runtime_unavailable" in _diagnostic_codes(result)

    def test_model_plan_exposes_capability_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            plan_module,
            "get_model_info",
            lambda *a, **k: (
                _fake_model(
                    status="beta",
                    experimental=True,
                    languages=("en", "fr"),
                ),
                None,
            ),
        )
        result = resolve_plan(
            ReadioConfig(),
            _text_request(SynthesisRequest(model="fake-en", allow_experimental=True)),
        )
        assert result.ok
        model = result.synthesis.model
        assert model.status == "beta"
        assert model.runtime_available is True
        assert model.languages == ("en", "fr")
        assert model.experimental is True
        payload = model.to_dict()
        assert payload["status"] == "beta"
        assert payload["runtime_available"] is True
        assert payload["languages"] == ["en", "fr"]
        assert payload["experimental"] is True


class TestConcreteFieldInvariant:
    def test_plan_without_model_is_not_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def no_concretization(candidate, *, cfg):
            return candidate, []

        monkeypatch.setattr(plan_module, "_concretize_backend_defaults", no_concretization)
        result = resolve_plan(ReadioConfig(), _text_request())
        assert not result.ok
        assert "synthesis_incomplete" in _diagnostic_codes(result)
        assert result.synthesis is None

    def test_missing_concrete_fields_error_instead_of_empty_strings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A model with no qualities and no default voice cannot produce a
        # concrete quality/voice; the plan must say so instead of "".
        monkeypatch.setattr(
            plan_module,
            "get_model_info",
            lambda *a, **k: (
                _fake_model(qualities=(), default_voice="", voices=()),
                None,
            ),
        )
        result = resolve_plan(
            ReadioConfig(),
            _text_request(SynthesisRequest(model="fake-en")),
        )
        assert not result.ok
        assert any(d.code == "synthesis_incomplete" for d in result.diagnostics)
        fields = {d.field for d in result.diagnostics if d.code == "synthesis_incomplete"}
        assert {"synthesis.quality", "synthesis.voice"} <= fields


class TestProvenanceCompleteness:
    def test_discovered_source_gets_a_decision(self) -> None:
        result = resolve_plan(
            ReadioConfig(),
            _text_request(SynthesisRequest(model="de-thorsten", language="de")),
        )
        assert result.ok
        source_decisions = [d for d in result.decisions if d.field == "synthesis.source"]
        assert source_decisions, "expected a synthesis.source decision"
        discovered = [d for d in source_decisions if d.origin == plan_module.ORIGIN_MODEL_DEFAULT]
        assert discovered, "discovered source fill must be recorded"
        assert result.synthesis.model.source == "github"
        assert any(d.value == "github" for d in discovered)

    def test_profile_allow_experimental_gets_a_decision(self) -> None:
        cfg = ReadioConfig(
            languages={
                "de": LanguageSettings(
                    model="de-crane",
                    allow_experimental=True,
                )
            }
        )
        result = resolve_plan(cfg, _text_request(SynthesisRequest(language="de")))
        assert result.ok
        decisions = [d for d in result.decisions if d.field == "synthesis.allow_experimental"]
        assert decisions, "profile allow_experimental must be recorded"
        decision = decisions[0]
        assert decision.value is True
        assert decision.origin in (
            plan_module.ORIGIN_CONFIG_LANGUAGE_EXACT,
            plan_module.ORIGIN_CONFIG_LANGUAGE_BASE,
        )
        assert decision.locator == "languages.de.allow_experimental"

    def test_every_effective_ssmd_binding_has_a_decision(self) -> None:
        cfg = ReadioConfig()
        text = (
            "---\n"
            "voice_bindings:\n"
            "  kokoro:\n"
            "    host: af_bella\n"
            "---\n"
            '<div voice="host">Doc.</div>\n'
            '<div voice="analyst">Role.</div>\n'
            '<div voice="guest">Invoked.</div>'
        )
        request = PlanRequest(
            operation="render",
            input=InputRequest(document=InputDocument(text=text, source_path=None, format="ssmd")),
            output=OutputRequest(),
            voice_bindings={"guest": "af_bella"},
        )
        result = resolve_plan(cfg, request)
        assert result.ok, [d.message for d in result.diagnostics]
        binding_fields = {f"ssmd.bindings.{b.reference}" for b in result.ssmd.bindings}
        decision_fields = {d.field for d in result.decisions}
        assert binding_fields, "expected resolved bindings"
        assert binding_fields <= decision_fields
        origins = {d.field: d.origin for d in result.decisions if d.field in binding_fields}
        assert origins["ssmd.bindings.host"] == plan_module.ORIGIN_DOCUMENT
        assert origins["ssmd.bindings.analyst"] == plan_module.ORIGIN_CONFIG_VOICE_ROLE
        assert origins["ssmd.bindings.guest"] == plan_module.ORIGIN_CLI


class TestInputSourceKind:
    def test_explicit_source_kind_wins(self) -> None:
        result = resolve_plan(ReadioConfig(), _text_request(source_kind="literal"))
        assert result.input.source_kind == "literal"

    def test_inference_falls_back_to_stdin_for_plain_documents(self) -> None:
        result = resolve_plan(ReadioConfig(), _text_request())
        assert result.input.source_kind == "stdin"

    def test_cli_literal_text_reports_literal(self, tmp_path) -> None:
        from readio.cli import _build_plan_request, build_parser

        args = build_parser().parse_args(["plan", "Hello world"])
        request = _build_plan_request(args, ReadioConfig())
        assert request.input.source_kind == "literal"

    def test_cli_stdin_reports_stdin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import sys
        from types import SimpleNamespace

        from readio.cli import _build_plan_request, build_parser

        monkeypatch.setattr(
            sys, "stdin", SimpleNamespace(isatty=lambda: False, read=lambda: "stdin text")
        )
        args = build_parser().parse_args(["plan"])
        request = _build_plan_request(args, ReadioConfig())
        assert request.input.source_kind == "stdin"

    def test_cli_file_reports_file(self, tmp_path) -> None:
        from readio.cli import _build_plan_request, build_parser

        source = tmp_path / "note.txt"
        source.write_text("Hello world.", encoding="utf-8")
        args = build_parser().parse_args(["plan", str(source)])
        request = _build_plan_request(args, ReadioConfig())
        assert request.input.source_kind == "file"


class TestEnvironmentPlan:
    def test_ffmpeg_available_is_populated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from readio import formats

        monkeypatch.setattr(formats, "ffmpeg_executable", lambda: "/usr/bin/ffmpeg")
        result = resolve_plan(ReadioConfig(), _text_request())
        assert result.environment.ffmpeg_available is True

    def test_ffmpeg_unavailable_is_populated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from readio import formats

        monkeypatch.setattr(formats, "ffmpeg_executable", lambda: None)
        result = resolve_plan(ReadioConfig(), _text_request())
        assert result.environment.ffmpeg_available is False


class TestOutputForceSemantics:
    def test_existing_output_warns_without_force(self, tmp_path) -> None:
        existing = tmp_path / "episode.wav"
        existing.write_bytes(b"x")
        request = PlanRequest(
            operation="render",
            input=InputRequest(
                document=InputDocument(text="Hello.", source_path=None, format="text")
            ),
            output=OutputRequest(requested_path=existing),
        )
        result = resolve_plan(ReadioConfig(), request)
        assert "output_exists" in _diagnostic_codes(result)
        assert result.output.force is False

    def test_force_represented_in_plan(self, tmp_path) -> None:
        existing = tmp_path / "episode.wav"
        existing.write_bytes(b"x")
        request = PlanRequest(
            operation="render",
            input=InputRequest(
                document=InputDocument(text="Hello.", source_path=None, format="text")
            ),
            output=OutputRequest(requested_path=existing, force=True),
        )
        result = resolve_plan(ReadioConfig(), request)
        assert result.output.force is True
        assert "output_exists" not in _diagnostic_codes(result)

    def test_explicit_path_without_suffix_is_completed(self, tmp_path) -> None:
        request = PlanRequest(
            operation="render",
            input=InputRequest(
                document=InputDocument(text="Hello.", source_path=None, format="text")
            ),
            output=OutputRequest(requested_path=tmp_path / "episode", requested_format="mp3"),
        )
        result = resolve_plan(ReadioConfig(), request)
        assert result.output.path == tmp_path / "episode.mp3"
        assert result.output.format == "mp3"


def test_plan_request_is_independent_of_argparse() -> None:
    # PlanRequest construction must not require argparse.Namespace.
    request = replace(_text_request(), operation="speak")
    assert request.operation == "speak"
