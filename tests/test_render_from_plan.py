from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import numpy as np

from readio import cli
from readio.api.speech import SpeechService
from readio.config import PathSettings, ReaderSettings, ReadioConfig
from readio.engines import (
    EngineCapabilities,
    EngineSelection,
    RenderedSpeech,
    SynthesisTarget,
)
from readio.engines.registry import _registry
from readio.plan import PlanDiagnostic


class _FakeSession:
    def __init__(self, adapter):
        self.adapter = adapter

    def synthesize(self, request):
        self.adapter.requests.append(request)
        return RenderedSpeech(
            id=request.id,
            audio=np.ones(240, dtype=np.float32),
            sample_rate=24000,
        )


class _FakeAdapter:
    id = "render-fixture"

    def __init__(self):
        self.open_calls = 0
        self.requests = []
        self.selections = []

    def version(self):
        return "1.0"

    def capabilities(self):
        return EngineCapabilities(
            id=self.id,
            voice_binding_namespace=self.id,
            supports_named_voices=True,
        )

    def discover(self, _request):
        return (
            SynthesisTarget(
                engine=self.id,
                id="fixture-model",
                display_name="Fixture",
                languages=("en-us",),
                voices=("fixture-voice",),
                sample_rate=24000,
            ),
        )

    def resolve(self, request):
        selection = EngineSelection(
            engine=self.id,
            target_id=request.target_id or "fixture-model",
            language=request.language or "en-us",
            voice=request.voice,
            options=dict(request.options),
        )
        self.selections.append(selection)
        return selection, ()

    def validate_selection(self, selection):
        if selection.target_id == "fixture-model":
            return ()
        return (
            PlanDiagnostic(
                code="fixture.target_invalid",
                severity="error",
                message=f"Unknown fixture target {selection.target_id!r}.",
                field="render.default_target.id",
            ),
        )

    def target_metadata(self, _selection):
        return {"languages": ("en-us",), "voices": ("fixture-voice",)}

    def canonical_synthesis_identity(self, selection):
        return {
            "engine": self.id,
            "target_id": selection.target_id,
            "voice": selection.voice,
            "options": dict(selection.options),
        }

    def open(self, _selection):
        self.open_calls += 1

        @contextmanager
        def session():
            yield _FakeSession(self)

        return session()


def _config(tmp_path: Path) -> ReadioConfig:
    return ReadioConfig(
        reader=ReaderSettings(engine="render-fixture", voice="fixture-voice", spacy="off"),
        paths=PathSettings(
            tmp_path / "templates",
            tmp_path / "ingest",
            tmp_path / "output",
        ),
    )


def _render(monkeypatch, tmp_path: Path, adapter: _FakeAdapter, argv: list[str]) -> int:
    monkeypatch.setitem(_registry._adapters, adapter.id, adapter)
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: _config(tmp_path))
    args = cli.build_parser().parse_args(["render", *argv, "--no-progress"])
    return cli._cmd_render(args)


def test_normal_render_executes_requests_from_the_resolved_plan(monkeypatch, tmp_path):
    adapter = _FakeAdapter()
    captured = []
    original_render = SpeechService.render

    def traced_render(self, request, **kwargs):
        result = original_render(self, request, **kwargs)
        captured.append(result)
        return result

    monkeypatch.setattr(SpeechService, "render", traced_render)
    output = tmp_path / "episode.wav"

    assert _render(monkeypatch, tmp_path, adapter, ["Hello world", "-o", str(output)]) == 0

    result = captured[0]
    assert result.plan.ok
    assert result.plan.render.default_target.id == "fixture-model"
    assert result.output_path == output
    assert output.is_file()
    assert adapter.open_calls == 1
    assert len(adapter.requests) == 1
    assert adapter.requests[0].text == "Hello world"


def test_invalid_target_fails_before_opening_the_engine(monkeypatch, tmp_path, capsys):
    adapter = _FakeAdapter()
    output = tmp_path / "invalid.wav"

    assert (
        _render(
            monkeypatch,
            tmp_path,
            adapter,
            ["Hello", "--model", "missing-model", "-o", str(output)],
        )
        == 1
    )

    assert adapter.open_calls == 0
    assert not output.exists()
    assert "fixture.target_invalid" in capsys.readouterr().out


def test_one_shot_render_does_not_create_a_project_tree(monkeypatch, tmp_path):
    adapter = _FakeAdapter()
    output = tmp_path / "hello.wav"

    assert _render(monkeypatch, tmp_path, adapter, ["Hello", "-o", str(output)]) == 0

    assert output.is_file()
    assert not tuple(tmp_path.rglob("project.json"))
    assert all(
        not (tmp_path / name).exists()
        for name in ("source", "document", "plan", "synthesis", "composition", "report")
    )


def test_render_resolves_ssmd_roles_before_lowering_requests(monkeypatch, tmp_path):
    adapter = _FakeAdapter()
    source = tmp_path / "cast.ssmd"
    source.write_text(
        "---\nssmd_version: '0.9'\n---\n\n:::{voice=\"host\"}\nHello from host.\n:::\n",
        encoding="utf-8",
    )
    output = tmp_path / "cast.wav"

    assert (
        _render(
            monkeypatch,
            tmp_path,
            adapter,
            [str(source), "--voice-bind", "host=fixture-voice", "-o", str(output)],
        )
        == 0
    )

    assert adapter.requests[0].voice == "fixture-voice"
    assert adapter.requests[0].text == "Hello from host."
