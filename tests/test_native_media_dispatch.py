from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import audiocompose
import pytest

from readio.api.types import ComposeRequest, ExportRequest
from readio.errors import ReadioStageError
from readio.integrations import audioexport as audioexport_integration
from readio.pipeline import compose, export
from readio.workspace import WorkspaceRef


def _workspace(tmp_path: Path, kind: Literal["document", "book"]) -> WorkspaceRef:
    return WorkspaceRef(root=tmp_path / "source.ssmd-bundle", kind=kind)


@pytest.mark.parametrize("kind", ["document", "book"])
def test_compose_forwards_unified_options_for_both_bundle_kinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: Literal["document", "book"]
) -> None:
    workspace = _workspace(tmp_path, kind)
    progress = SimpleNamespace(completed=1, total=2, phase="compose.bundle")
    captured: dict[str, Any] = {}
    events: list[object] = []
    result = object()

    def compose_bundle(path: Path, **kwargs: Any) -> object:
        captured.update(path=path, **kwargs)
        kwargs["on_progress"](progress)
        return result

    monkeypatch.setattr(audiocompose, "compose_bundle", compose_bundle)
    request = ComposeRequest(
        variant="narration--exact",
        chapters="3-6",
        pause_policy=tmp_path / "pauses.json",
        options=audiocompose.BundleComposeOptions(
            sample_rate=48000,
            clip_policy="warn",
            target_lufs=-18.0,
            true_peak_ceiling_dbtp=-2.0,
            peak_policy="error",
        ),
        renderplan_root=tmp_path / "renderplan",
        voicerender_root=tmp_path / "voicerender",
        output_root=tmp_path / "audiocompose",
        media_root=tmp_path / "media",
        save_audiojobs=tmp_path / "audiojobs",
        force=True,
        fail_fast=True,
        emphasis_policy="strict",
    )

    actual = compose(workspace, request, on_progress=events.append)

    assert actual is result
    assert captured["path"] == workspace.root
    assert captured["variant"] == "narration--exact"
    options = captured["options"]
    assert isinstance(options, audiocompose.BundleComposeOptions)
    assert options.chapters == "3-6"
    assert options.pauses == tmp_path / "pauses.json"
    assert options.renderplan_root == tmp_path / "renderplan"
    assert options.voicerender_root == tmp_path / "voicerender"
    assert options.output_root == tmp_path / "audiocompose"
    assert options.media_root == tmp_path / "media"
    assert options.save_audiojobs == tmp_path / "audiojobs"
    assert options.sample_rate == 48000
    assert options.clip_policy == "warn"
    assert options.target_lufs == -18.0
    assert options.true_peak_ceiling_dbtp == -2.0
    assert options.peak_policy == "error"
    assert options.force is True
    assert options.fail_fast is True
    assert options.emphasis_policy == "strict"
    assert events[0].stage == "compose"
    assert events[0].current == 1
    assert events[0].total == 2
    assert events[0].source is progress


def test_compose_maps_public_output_policy_to_bundle_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, "book")
    captured: dict[str, Any] = {}

    def compose_bundle(path: Path, **kwargs: Any) -> object:
        captured.update(path=path, **kwargs)
        return object()

    monkeypatch.setattr(audiocompose, "compose_bundle", compose_bundle)
    policy = audiocompose.OutputPolicy(
        sample_rate=32000,
        channels=1,
        clip_policy="warn",
        loudness=audiocompose.LoudnessPolicy(target_lufs=-17.0, true_peak_ceiling_dbtp=-1.5),
    )

    compose(workspace, ComposeRequest(variant="v", output_policy=policy))

    options = captured["options"]
    assert options.sample_rate == 32000
    assert options.clip_policy == "warn"
    assert options.target_lufs == -17.0
    assert options.true_peak_ceiling_dbtp == -1.5


def test_export_forwards_the_unified_request_to_lazy_package_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, "document")
    result = object()
    captured: dict[str, Any] = {}

    def export_bundle(path: Path, **kwargs: Any) -> object:
        captured.update(path=path, **kwargs)
        return result

    def fake_import(name: str) -> Any:
        assert name == "audioexport"
        return SimpleNamespace(export_bundle=export_bundle)

    monkeypatch.setattr(audioexport_integration, "import_module", fake_import)
    request = ExportRequest(
        variant="narration--exact",
        chapters="all",
        output=tmp_path / "article.flac",
        format="flac",
        profile=tmp_path / "profile.toml",
        bitrate="512k",
        sample_rate=48000,
        channels=2,
        metadata={"title": "Article"},
        cover=tmp_path / "cover.png",
        timeline=tmp_path / "timeline.json",
        audiocompose_root=tmp_path / "custom-compose",
        output_root=tmp_path / "custom-export",
        force=True,
        ffmpeg=tmp_path / "ffmpeg",
        ffprobe=tmp_path / "ffprobe",
    )

    actual = export(workspace, request)

    assert actual is result
    assert captured == {
        "path": workspace.root,
        "variant": "narration--exact",
        "chapters": "all",
        "output": tmp_path / "article.flac",
        "format": "flac",
        "profile": tmp_path / "profile.toml",
        "bitrate": "512k",
        "sample_rate": 48000,
        "channels": 2,
        "metadata": {"title": "Article"},
        "cover": tmp_path / "cover.png",
        "timeline": tmp_path / "timeline.json",
        "audiocompose_root": tmp_path / "custom-compose",
        "output_root": tmp_path / "custom-export",
        "force": True,
        "ffmpeg": tmp_path / "ffmpeg",
        "ffprobe": tmp_path / "ffprobe",
    }


def test_missing_optional_audioexport_reports_extra_install_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing_package(name: str) -> Any:
        raise ModuleNotFoundError("No module named 'audioexport'", name=name)

    monkeypatch.setattr(audioexport_integration, "import_module", missing_package)

    with pytest.raises(ReadioStageError, match="install Readio with the 'audioexport' extra"):
        export(_workspace(tmp_path, "document"), ExportRequest(variant="v"))
