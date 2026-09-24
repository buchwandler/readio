from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from readio import cli
from readio.api.speech import SpeechService
from readio.api.types import RenderResult
from readio.audio import RenderSummary
from readio.config import PathSettings, ReadioConfig
from readio.errors import ManifestError
from readio.manifest import RENDER_MANIFEST_SCHEMA_V2, manifest_path_for


def _workspace_cfg(tmp_path: Path) -> ReadioConfig:
    return ReadioConfig(
        paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "output")
    )


def _install_render_stub(monkeypatch, calls: list[tuple[object, bool]]) -> None:
    def plan(self, request):
        output = request.output.requested_path
        assert output is not None
        audio_format = request.output.requested_format or output.suffix.lstrip(".")
        return SimpleNamespace(
            ok=True,
            output=SimpleNamespace(
                path=output,
                format=audio_format,
                force=request.output.force,
            ),
            to_dict=lambda: {"ok": True},
        )

    def render(self, request, *, on_event=None, write_manifest=False):
        resolved = self.plan(request)
        output = resolved.output.path
        output.write_bytes(b"encoded audio")
        manifest_path = manifest_path_for(output) if write_manifest else None
        if manifest_path is not None:
            manifest_path.write_text("{}", encoding="utf-8")
        calls.append((resolved, write_manifest))
        return RenderResult(
            plan=resolved,
            summary=RenderSummary(sample_rate=24000, sample_count=48000, channels=1),
            output_path=output,
            manifest_path=manifest_path,
            audio_format=resolved.output.format,
            manifest_schema=RENDER_MANIFEST_SCHEMA_V2 if manifest_path is not None else None,
        )

    monkeypatch.setattr(SpeechService, "plan", plan)
    monkeypatch.setattr(SpeechService, "render", render)


def test_render_cli_forwards_manifest_request_and_keeps_human_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path
) -> None:
    cfg = _workspace_cfg(tmp_path)
    output = tmp_path / "episode.wav"
    calls: list[tuple[object, bool]] = []
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: cfg)
    _install_render_stub(monkeypatch, calls)
    args = cli.build_parser().parse_args(
        ["render", "Hello world", "-o", str(output), "--manifest", "--no-progress"]
    )

    assert cli._cmd_render(args) == 0
    assert output.read_bytes() == b"encoded audio"
    assert calls[0][1] is True
    manifest = json.loads(manifest_path_for(output).read_text(encoding="utf-8"))
    assert manifest == {}
    assert capsys.readouterr().out == f"{output}\n"


def test_render_json_reports_optional_manifest_from_service(
    monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path
) -> None:
    cfg = _workspace_cfg(tmp_path)
    calls: list[tuple[object, bool]] = []
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: cfg)
    _install_render_stub(monkeypatch, calls)

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
        "schema": RENDER_MANIFEST_SCHEMA_V2,
        "path": str(manifest_path_for(output_with)),
    }
    assert [enabled for _, enabled in calls] == [False, True]


def test_manifest_is_rejected_for_live_before_config_or_speech_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: pytest.fail("config resolved"))
    monkeypatch.setattr(
        SpeechService,
        "plan",
        lambda *_args, **_kwargs: pytest.fail("speech planned"),
    )
    args = cli.build_parser().parse_args(["render", "--live", "--manifest", "--no-progress"])

    with pytest.raises(ValueError, match="does not execute a bounded ReadioPlan"):
        cli._cmd_render(args)


def test_render_errors_from_public_service_propagate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_resolved_config", lambda _args: _workspace_cfg(tmp_path))
    _install_render_stub(monkeypatch, [])
    output = tmp_path / "episode.wav"
    error = ManifestError(
        "manifest write failed",
        audio_path=output,
        manifest_path=manifest_path_for(output),
    )
    monkeypatch.setattr(
        SpeechService,
        "render",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )
    args = cli.build_parser().parse_args(
        ["render", "text", "-o", str(tmp_path / "episode.wav"), "--manifest"]
    )

    with pytest.raises(ManifestError, match="manifest write failed"):
        cli._cmd_render(args)
