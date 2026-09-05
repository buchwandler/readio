from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from readio import cli
from readio.audio import RenderSummary
from readio.config import PathSettings, ReadioConfig
from readio.errors import ManifestError
from readio.manifest import MANIFEST_SCHEMA, manifest_path_for


def _workspace_cfg(tmp_path: Path) -> ReadioConfig:
    return ReadioConfig(
        paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "output")
    )


@contextmanager
def _sink_passthrough(path: Path, audio_format: str):
    yield path


def _stub_render(summary: RenderSummary | None = None):
    def render(plan, document, path, *, selector="all", **kwargs):
        path.write_bytes(b"encoded audio")
        return summary or RenderSummary(sample_rate=24000, sample_count=48000, channels=1)

    return render


def _fake_plan(output: Path, *, text: str, force: bool = False):
    resolved = {
        "schema": "readio.plan.v1",
        "ok": True,
        "operation": "render",
        "input": {"text": text},
        "output": {
            "format": "wav",
            "encoder_backend": "soundfile",
            "path": str(output),
            "force": force,
        },
    }
    return type(
        "FakePlan",
        (),
        {
            "ok": True,
            "output": type(
                "FakeOutput",
                (),
                {
                    "path": output,
                    "format": "wav",
                    "encoder_backend": "soundfile",
                    "force": force,
                },
            )(),
            "to_dict": lambda self: resolved,
        },
    )()


def _patch_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
        "resolve_plan",
        lambda config, request: _fake_plan(
            request.output.requested_path,
            text=request.input.document.text,
            force=request.output.force,
        ),
    )


def test_manifest_render_embeds_exact_plan_and_preserves_human_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path
) -> None:
    cfg = _workspace_cfg(tmp_path)
    output = tmp_path / "episode.wav"
    captured: dict[str, object] = {}
    resolve_calls = 0

    monkeypatch.setattr(cli, "_resolved_config", lambda args: cfg)
    monkeypatch.setattr(cli, "create_audio_sink", _sink_passthrough)
    monkeypatch.setattr(
        cli,
        "render_from_plan",
        _stub_render(
            RenderSummary(
                sample_rate=24000,
                sample_count=48000,
                channels=1,
                document_metadata={"title": "Überblick"},
                markers=({"label": "start", "sample_offset": 12},),
            )
        ),
    )

    def resolve(config, request):
        nonlocal resolve_calls
        resolve_calls += 1
        plan = _fake_plan(
            request.output.requested_path,
            text=request.input.document.text,
            force=request.output.force,
        )
        captured["plan"] = plan
        return plan

    monkeypatch.setattr(cli, "resolve_plan", resolve)
    args = cli.build_parser().parse_args(
        ["render", "Hello world", "-o", str(output), "--manifest", "--no-progress"]
    )

    assert cli._cmd_render(args) == 0

    sidecar = manifest_path_for(output)
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    assert resolve_calls == 1
    assert manifest["schema"] == MANIFEST_SCHEMA
    assert manifest["plan"]["resolved"] == captured["plan"].to_dict()
    assert manifest["result"]["output"]["sha256"]
    assert manifest["result"]["document_metadata"] == {"title": "Überblick"}
    assert manifest["result"]["markers"] == [{"label": "start", "sample_offset": 12}]
    assert capsys.readouterr().out == f"{output}\n"


def test_json_render_result_is_additive_with_and_without_manifest(
    monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path
) -> None:
    cfg = _workspace_cfg(tmp_path)
    monkeypatch.setattr(cli, "_resolved_config", lambda args: cfg)
    monkeypatch.setattr(cli, "create_audio_sink", _sink_passthrough)
    monkeypatch.setattr(cli, "render_from_plan", _stub_render())
    _patch_plan(monkeypatch)

    output_without = tmp_path / "without.wav"
    args_without = cli.build_parser().parse_args(
        ["render", "text", "-o", str(output_without), "--json", "--no-progress"]
    )
    assert cli._cmd_render(args_without) == 0
    result_without = json.loads(capsys.readouterr().out)
    assert result_without["manifest"] is None
    assert not manifest_path_for(output_without).exists()

    output_with = tmp_path / "with.wav"
    args_with = cli.build_parser().parse_args(
        [
            "render",
            "text",
            "-o",
            str(output_with),
            "--json",
            "--manifest",
            "--no-progress",
        ]
    )
    assert cli._cmd_render(args_with) == 0
    result_with = json.loads(capsys.readouterr().out)
    assert result_with["manifest"] == {
        "schema": MANIFEST_SCHEMA,
        "path": str(manifest_path_for(output_with)),
    }


def test_manifest_write_failure_preserves_audio_and_exposes_both_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _workspace_cfg(tmp_path)
    output = tmp_path / "episode.wav"
    monkeypatch.setattr(cli, "_resolved_config", lambda args: cfg)
    monkeypatch.setattr(cli, "create_audio_sink", _sink_passthrough)
    monkeypatch.setattr(cli, "render_from_plan", _stub_render())
    _patch_plan(monkeypatch)

    def fail_write(path, payload):
        raise OSError("read-only sidecar directory")

    monkeypatch.setattr(cli, "write_render_manifest", fail_write)
    args = cli.build_parser().parse_args(
        ["render", "text", "-o", str(output), "--manifest", "--no-progress"]
    )

    with pytest.raises(ManifestError) as raised:
        cli._cmd_render(args)

    error = raised.value
    assert error.code == "render.manifest_error"
    assert output.exists()
    assert not manifest_path_for(output).exists()
    assert cli._error_payload(error) == {
        "ok": False,
        "code": "render.manifest_error",
        "error": str(error),
        "audio_path": str(output),
        "manifest_path": str(manifest_path_for(output)),
    }


def test_manifest_is_rejected_for_live_before_synthesis_or_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_resolved_config", lambda args: pytest.fail("config resolved"))
    monkeypatch.setattr(cli, "resolve_synthesis", lambda *args: pytest.fail("TTS resolved"))
    args = cli.build_parser().parse_args(["render", "--live", "--manifest", "--no-progress"])

    with pytest.raises(ValueError, match="does not execute a bounded ReadioPlan"):
        cli._cmd_render(args)


def test_failed_render_does_not_create_audio_or_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _workspace_cfg(tmp_path)
    output = tmp_path / "episode.wav"
    monkeypatch.setattr(cli, "_resolved_config", lambda args: cfg)
    monkeypatch.setattr(cli, "create_audio_sink", _sink_passthrough)
    _patch_plan(monkeypatch)

    def fail_render(plan, document, path, *, selector="all", **kwargs):
        path.write_bytes(b"temporary audio")
        raise RuntimeError("synthesis failed")

    monkeypatch.setattr(cli, "render_from_plan", fail_render)
    args = cli.build_parser().parse_args(
        ["render", "text", "-o", str(output), "--manifest", "--no-progress"]
    )

    with pytest.raises(RuntimeError, match="synthesis failed"):
        cli._cmd_render(args)

    assert not output.exists()
    assert not manifest_path_for(output).exists()


def test_force_rerender_replaces_manifest_with_new_execution_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _workspace_cfg(tmp_path)
    output = tmp_path / "episode.wav"
    monkeypatch.setattr(cli, "_resolved_config", lambda args: cfg)
    monkeypatch.setattr(cli, "create_audio_sink", _sink_passthrough)
    _patch_plan(monkeypatch)

    calls = 0

    def render(plan, document, path, *, selector="all", **kwargs):
        nonlocal calls
        calls += 1
        path.write_bytes(f"encoded audio {calls}".encode())
        return RenderSummary(sample_rate=24000, sample_count=24000, channels=1)

    monkeypatch.setattr(cli, "render_from_plan", render)
    first = cli.build_parser().parse_args(
        ["render", "first", "-o", str(output), "--manifest", "--no-progress"]
    )
    assert cli._cmd_render(first) == 0
    first_manifest = json.loads(manifest_path_for(output).read_text(encoding="utf-8"))

    second = cli.build_parser().parse_args(
        [
            "render",
            "second",
            "-o",
            str(output),
            "--force",
            "--manifest",
            "--no-progress",
        ]
    )
    assert cli._cmd_render(second) == 0
    second_manifest = json.loads(manifest_path_for(output).read_text(encoding="utf-8"))

    assert (
        first_manifest["result"]["output"]["sha256"]
        != second_manifest["result"]["output"]["sha256"]
    )
