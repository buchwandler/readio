"""Regression tests: normal `readio render` resolves and executes the ReadioPlan.

These tests exercise the actual normal render path (`cli._cmd_render`) with the
synthesis backend mocked, and assert that the PipelineConfig, output path, and
SSMD render bindings used by rendering are exactly the values from the plan.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import numpy as np
import pytest
from audiocompose import AudioBufferSource, AudioClip, AudioJob

from readio import cli, reader
from readio.api import PlanRequest
from readio.api import speech as speech_module
from readio.api.speech import SpeechService
from readio.audio import RenderSummary
from readio.config import PathSettings, ReadioConfig


def _workspace_cfg(tmp_path: Path) -> ReadioConfig:
    return ReadioConfig(
        paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "output")
    )


class _FakePipeline:
    """Records construction order and captures the PipelineConfig."""

    instances: ClassVar[list[_FakePipeline]] = []
    events: ClassVar[list[str]] = []

    def __init__(self, config) -> None:
        self.config = config
        _FakePipeline.instances.append(self)
        _FakePipeline.events.append("load_tts")

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def prepare_units(self, text: str, unit=None):
        @contextmanager
        def ctx():
            yield SimpleNamespace(units=())

        return ctx()

    def to_audio_job_from_plan(self, plan, **overrides):
        unit = plan.units[0]
        return AudioJob(
            items=(
                AudioClip(
                    id=unit.id,
                    source=AudioBufferSource(np.ones(8, dtype=np.float32), 24000),
                ),
            ),
        )


@pytest.fixture
def fake_tts(monkeypatch: pytest.MonkeyPatch):
    _FakePipeline.instances = []
    _FakePipeline.events = []
    monkeypatch.setattr("pykokoro.KokoroPipeline", _FakePipeline)
    monkeypatch.setattr(
        reader,
        "render_prepared",
        lambda prepared, sink, indices=None, on_progress=None: RenderSummary(
            sample_rate=24000, sample_count=48000, channels=1
        ),
    )
    return _FakePipeline


def _render(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, argv: list[str]) -> int:
    monkeypatch.setattr(cli, "_resolved_config", lambda args: _workspace_cfg(tmp_path))
    args = cli.build_parser().parse_args(argv)
    return cli._cmd_render(args)


def _capture_plan(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    captured: dict[str, object] = {}
    original = SpeechService.render

    def traced(self, request, **kwargs):
        result = original(self, request, **kwargs)
        captured["plan"] = result.plan
        captured["request"] = request
        return result

    monkeypatch.setattr(SpeechService, "render", traced)
    return captured

def test_normal_render_uses_public_speech_service_before_loading_tts(
    monkeypatch: pytest.MonkeyPatch, fake_tts, tmp_path: Path
) -> None:
    events = fake_tts.events
    original = SpeechService.render

    def traced(self, request, **kwargs):
        events.append("speech.render")
        return original(self, request, **kwargs)

    monkeypatch.setattr(SpeechService, "render", traced)
    output = tmp_path / "episode.wav"
    code = _render(
        monkeypatch, tmp_path, ["render", "Hello world", "-o", str(output), "--no-progress"]
    )

    assert code == 0
    assert events == ["speech.render", "load_tts"]

def test_normal_render_uses_plan_pipeline_config(
    monkeypatch: pytest.MonkeyPatch, fake_tts, tmp_path: Path
) -> None:
    captured = _capture_plan(monkeypatch)
    output = tmp_path / "episode.wav"
    code = _render(
        monkeypatch,
        tmp_path,
        [
            "render",
            "Hello world",
            "--voice",
            "af_bella",
            "--speed",
            "1.3",
            "--spacy",
            "lg",
            "--short-sentence",
            "wrap",
            "-o",
            str(output),
            "--no-progress",
        ],
    )
    assert code == 0
    used = fake_tts.instances[0].config
    plan = captured["plan"]
    assert used.model_variant == plan.render.target.id
    assert used.model_source == plan.render.options["model_source"]
    assert used.model_quality == plan.render.options["quality"]
    assert used.voice == plan.render.target.voice
    assert used.generation.lang == plan.render.target.language
    assert used.generation.speed == plan.render.rate
    assert used.generation.pause_mode == plan.render.options["pause_mode"]
    assert used.allow_experimental_frontend == plan.render.options["allow_experimental"]
    assert plan.planning.spacy == "lg"
    assert plan.render.options["short_sentence"] == "wrap"
    assert used.tokenizer_config is None
    assert used.short_sentence_config is not None
    assert used.short_sentence_config.resolve_mode == "wrap"



def test_normal_render_uses_plan_output_path(
    monkeypatch: pytest.MonkeyPatch, fake_tts, tmp_path: Path
) -> None:
    captured = _capture_plan(monkeypatch)
    sink_targets: list[Path] = []
    real_atomic = speech_module.atomic_audio_path

    @contextmanager
    def traced_atomic(output: Path, *, force: bool):
        sink_targets.append(output)
        with real_atomic(output, force=force) as temporary:
            yield temporary

    monkeypatch.setattr(speech_module, "atomic_audio_path", traced_atomic)
    # No -o: the path is generated by the plan once.
    code = _render(monkeypatch, tmp_path, ["render", "Hello world", "--no-progress"])

    assert code == 0
    plan = captured["plan"]
    assert plan.output.path_origin == "generated"
    assert sink_targets == [plan.output.path]


def test_normal_render_uses_plan_ssmd_bindings(
    monkeypatch: pytest.MonkeyPatch, fake_tts, tmp_path: Path
) -> None:
    source = tmp_path / "cast.ssmd"
    source.write_text(
        '<div voice="host">Hello from host.</div>\n<div voice="analyst">Analysis.</div>',
        encoding="utf-8",
    )
    output = tmp_path / "cast.wav"
    code = _render(
        monkeypatch,
        tmp_path,
        [
            "render",
            str(source),
            "--voice-bind",
            "analyst=am_michael",
            "-o",
            str(output),
            "--no-progress",
        ],
    )
    assert code == 0
    used = fake_tts.instances[0].config
    expected = dict(used.ssmd.voice_bindings["kokoro"])
    assert expected == {"host": "af_sarah", "analyst": "am_michael"}
    used = fake_tts.instances[0].config
    assert dict(used.ssmd.voice_bindings["kokoro"]) == expected


def test_normal_render_does_not_expose_legacy_synthesis_resolver(
    monkeypatch: pytest.MonkeyPatch, fake_tts, tmp_path: Path
) -> None:
    assert not hasattr(cli, "resolve_synthesis")
    output = tmp_path / "episode.wav"
    code = _render(
        monkeypatch, tmp_path, ["render", "Hello world", "-o", str(output), "--no-progress"]
    )
    assert code == 0

def test_normal_render_does_not_reallocate_output_path(
    monkeypatch: pytest.MonkeyPatch, fake_tts, tmp_path: Path
) -> None:
    from readio import paths as paths_module

    calls: list[object] = []
    real_resolve = paths_module.resolve_render_output

    def traced(cfg, *, explicit, input_path, audio_format):
        calls.append(input_path)
        return real_resolve(
            cfg, explicit=explicit, input_path=input_path, audio_format=audio_format
        )

    monkeypatch.setattr(paths_module, "resolve_render_output", traced)
    code = _render(monkeypatch, tmp_path, ["render", "Hello world", "--no-progress"])

    assert code == 0
    # The path is allocated exactly once, by the plan.
    assert len(calls) == 1


def test_normal_render_invalid_plan_fails_before_tts(
    monkeypatch: pytest.MonkeyPatch, fake_tts, tmp_path: Path, capsys
) -> None:
    output = tmp_path / "episode.wav"
    output.write_bytes(b"existing")
    code = _render(
        monkeypatch,
        tmp_path,
        ["render", "Hello world", "--model", "de-thorsten", "-o", str(output), "--no-progress"],
    )
    assert code == 1
    assert fake_tts.instances == []
    captured = capsys.readouterr().out
    assert "model_language_incompatible" in captured


def test_normal_render_uses_public_service_request_and_result(
    monkeypatch: pytest.MonkeyPatch, fake_tts, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    original = SpeechService.render

    def traced(self, request, **kwargs):
        result = original(self, request, **kwargs)
        captured["request"] = request
        captured["result"] = result
        return result

    monkeypatch.setattr(SpeechService, "render", traced)
    output = tmp_path / "episode.wav"
    code = _render(
        monkeypatch, tmp_path, ["render", "Hello world", "-o", str(output), "--no-progress"]
    )

    assert code == 0
    request = captured["request"]
    result = captured["result"]
    assert isinstance(request, PlanRequest)
    assert request.output.mode == "file"
    assert result.output_path == output
    assert result.plan.output.path == output

def test_one_shot_render_does_not_create_project_tree(
    monkeypatch: pytest.MonkeyPatch, fake_tts, tmp_path: Path
) -> None:
    output = tmp_path / "hello.wav"

    code = _render(
        monkeypatch,
        tmp_path,
        ["render", "Hello", "-o", str(output), "--no-progress"],
    )

    assert code == 0
    assert output.is_file()
    assert not any(
        path.is_file() and path.name == "project.json" for path in tmp_path.rglob("project.json")
    )
    for name in ("source", "document", "plan", "synthesis", "composition", "report"):
        assert not (tmp_path / name).exists()
