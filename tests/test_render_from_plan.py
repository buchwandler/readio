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

import pytest

from readio import cli, reader
from readio.audio import RenderSummary
from readio.config import PathSettings, ReadioConfig
from readio.plan import PlanRequest, resolve_plan


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
    """Wrap cli.resolve_plan to capture the plan resolved during the render."""
    captured: dict[str, object] = {}
    original = cli.resolve_plan

    def traced(cfg, request: PlanRequest):
        plan = original(cfg, request)
        captured["plan"] = plan
        captured["request"] = request
        return plan

    monkeypatch.setattr(cli, "resolve_plan", traced)
    return captured


def test_normal_render_resolves_plan_before_loading_tts(
    monkeypatch: pytest.MonkeyPatch, fake_tts, tmp_path: Path
) -> None:
    events: list[str] = []
    original = cli.resolve_plan

    def traced(cfg, request: PlanRequest):
        events.append("resolve_plan")
        return original(cfg, request)

    monkeypatch.setattr(cli, "resolve_plan", traced)
    output = tmp_path / "episode.wav"
    code = _render(
        monkeypatch, tmp_path, ["render", "Hello world", "-o", str(output), "--no-progress"]
    )

    assert code == 0
    assert events == ["resolve_plan"]
    assert fake_tts.events == ["load_tts"]
    assert events + fake_tts.events == ["resolve_plan", "load_tts"]


def test_normal_render_uses_plan_pipeline_config(
    monkeypatch: pytest.MonkeyPatch, fake_tts, tmp_path: Path
) -> None:
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
            "-o",
            str(output),
            "--no-progress",
        ],
    )
    assert code == 0
    used = fake_tts.instances[0].config
    # Independently resolve the plan for the same request and compare.
    expected = resolve_plan(
        _workspace_cfg(tmp_path), _plan_request_for("Hello world", "af_bella", 1.3)
    )
    assert expected.ok
    assert used.model_variant == expected.synthesis.model.id
    assert used.model_source == expected.synthesis.model.source
    assert used.model_quality == expected.synthesis.model.quality
    assert used.voice == expected.synthesis.model.voice
    assert used.generation.lang == expected.synthesis.language
    assert used.generation.speed == expected.synthesis.speed
    assert used.generation.pause_mode == expected.synthesis.pause_mode
    assert used.allow_experimental_frontend == expected.synthesis.allow_experimental


def _plan_request_for(text: str, voice: str, speed: float) -> PlanRequest:
    from readio.document import InputDocument
    from readio.plan import InputRequest, OutputRequest, SynthesisRequest

    return PlanRequest(
        operation="render",
        input=InputRequest(document=InputDocument(text=text, source_path=None, format="text")),
        synthesis=SynthesisRequest(voice=voice, speed=speed),
        output=OutputRequest(),
    )


def test_normal_render_uses_plan_output_path(
    monkeypatch: pytest.MonkeyPatch, fake_tts, tmp_path: Path
) -> None:
    captured = _capture_plan(monkeypatch)
    sink_targets: list[Path] = []
    real_atomic = cli.atomic_audio_path

    @contextmanager
    def traced_atomic(output: Path, *, force: bool):
        sink_targets.append(output)
        with real_atomic(output, force=force) as temporary:
            yield temporary

    monkeypatch.setattr(cli, "atomic_audio_path", traced_atomic)
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
    captured = _capture_plan(monkeypatch)
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
    plan = captured["plan"]
    expected = {binding.reference: binding.voice for binding in plan.ssmd.bindings}
    assert expected == {"host": "af_sarah", "analyst": "am_michael"}
    used = fake_tts.instances[0].config
    assert dict(used.ssmd.voice_bindings["kokoro"]) == expected


def test_normal_render_does_not_call_resolve_synthesis(
    monkeypatch: pytest.MonkeyPatch, fake_tts, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        cli,
        "resolve_synthesis",
        lambda *a, **k: pytest.fail("old resolver must not be used by normal render"),
    )
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


def test_normal_render_passes_an_audio_sink_not_a_path(
    monkeypatch: pytest.MonkeyPatch, fake_tts, tmp_path: Path
) -> None:
    """The plan render path must wrap the atomic temporary file in a sink.

    Regression guard: an earlier version passed the temporary ``Path`` directly
    to ``render_from_plan``, which only mocked backends hid until a real render
    crashed with ``AttributeError: 'PosixPath' object has no attribute 'write'``.
    """
    received: dict[str, object] = {}
    real_render = cli.render_from_plan

    def traced_render(plan, document, sink, *, selector="all", **kwargs):
        received["sink"] = sink
        return real_render(plan, document, sink, selector=selector, **kwargs)

    monkeypatch.setattr(cli, "render_from_plan", traced_render)
    output = tmp_path / "episode.wav"
    code = _render(
        monkeypatch, tmp_path, ["render", "Hello world", "-o", str(output), "--no-progress"]
    )

    assert code == 0
    sink = received["sink"]
    assert not isinstance(sink, Path)
    assert callable(getattr(sink, "write", None))
