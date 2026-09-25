"""Validation checks for the request-centric plan-v2 contract."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from readio import formats
from readio.api import Readio
from readio.config import ReadioConfig
from readio.document import InputDocument
from readio.plan import InputRequest, OutputRequest, PlanRequest, SynthesisRequest, resolve_plan


def _request(
    synthesis: SynthesisRequest | None = None,
    *,
    source_kind: str | None = None,
    output: OutputRequest | None = None,
) -> PlanRequest:
    return PlanRequest(
        operation="render",
        input=InputRequest(
            document=InputDocument("Hello world.", None, "text"),
            source_kind=source_kind,
        ),
        synthesis=synthesis or SynthesisRequest(),
        output=output or OutputRequest(),
    )


def _diagnostic_codes(plan) -> set[str]:
    return {item.code for item in plan.diagnostics}


def test_incompatible_target_language_is_reported_by_its_adapter():
    plan = resolve_plan(
        ReadioConfig(),
        _request(SynthesisRequest(model="de-thorsten")),
    )

    assert not plan.ok
    diagnostic = next(
        item for item in plan.diagnostics if item.code == "pykokoro.language_incompatible"
    )
    assert "de" in diagnostic.message


def test_matching_target_language_resolves_in_plan_v2():
    plan = resolve_plan(
        ReadioConfig(),
        _request(SynthesisRequest(model="de-thorsten", language="de")),
    )

    assert plan.ok
    assert plan.planning.language == "de"
    assert plan.render.default_target.id == "de-thorsten"


def test_explicit_source_kind_is_preserved():
    plan = resolve_plan(ReadioConfig(), _request(source_kind="literal"))

    assert plan.input.source_kind == "literal"


def test_cli_infers_literal_stdin_and_file_source_kinds(tmp_path, monkeypatch):
    import sys

    from readio.cli import _build_plan_request, build_parser

    app = Readio(ReadioConfig())
    literal = build_parser().parse_args(["render", "Hello world"])
    assert _build_plan_request(literal, app).input.source_kind == "literal"

    monkeypatch.setattr(
        sys, "stdin", SimpleNamespace(isatty=lambda: False, read=lambda: "stdin text")
    )
    stdin = build_parser().parse_args(["render"])
    assert _build_plan_request(stdin, app).input.source_kind == "stdin"

    source = tmp_path / "note.txt"
    source.write_text("Hello world.", encoding="utf-8")
    file_request = build_parser().parse_args(["render", str(source)])
    assert _build_plan_request(file_request, app).input.source_kind == "file"


def test_environment_reports_ffmpeg_availability(monkeypatch):
    monkeypatch.setattr(formats, "ffmpeg_executable", lambda: "/usr/bin/ffmpeg")
    available = resolve_plan(ReadioConfig(), _request())
    monkeypatch.setattr(formats, "ffmpeg_executable", lambda: None)
    unavailable = resolve_plan(ReadioConfig(), _request())

    assert available.environment.ffmpeg_available is True
    assert unavailable.environment.ffmpeg_available is False


def test_existing_output_requires_force_and_force_is_recorded(tmp_path: Path):
    path = tmp_path / "episode.wav"
    path.write_bytes(b"x")
    request = _request(output=OutputRequest(requested_path=path))
    blocked = resolve_plan(ReadioConfig(), request)
    forced = resolve_plan(
        ReadioConfig(),
        replace(request, output=OutputRequest(requested_path=path, force=True)),
    )

    assert "output_exists" in _diagnostic_codes(blocked)
    assert blocked.output.force is False
    assert "output_exists" not in _diagnostic_codes(forced)
    assert forced.output.force is True


def test_explicit_output_path_without_suffix_uses_requested_format(tmp_path: Path):
    plan = resolve_plan(
        ReadioConfig(),
        _request(
            output=OutputRequest(
                requested_path=tmp_path / "episode",
                requested_format="mp3",
            )
        ),
    )

    assert plan.output.path == tmp_path / "episode.mp3"
    assert plan.output.format == "mp3"


def test_plan_request_does_not_depend_on_argparse():
    request = replace(_request(), operation="speak")

    assert request.operation == "speak"
