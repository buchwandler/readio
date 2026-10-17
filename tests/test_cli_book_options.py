from __future__ import annotations

import json
from pathlib import Path

from readio import cli
from readio.book import BookBuildResult, BookProgressEvent


def test_audiobook_build_cli_forwards_supported_specialist_options(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workspace = tmp_path / "book.ssmdbook"
    profile = tmp_path / "narrator.renderprofile.toml"
    captured: dict[str, object] = {}
    result = BookBuildResult(
        workspace=workspace,
        variant="exact-variant",
        compile_result=object(),
    )

    def build(request, *, on_progress=None):
        captured["request"] = request
        captured["callback"] = on_progress
        if on_progress is not None:
            on_progress(
                BookProgressEvent("synth", "progress", current=4, total=7, message="rendering")
            )
        return result

    monkeypatch.setattr(cli.api, "build_book", build)
    args = [
        "audiobook",
        "build",
        str(workspace),
        "--profile",
        str(profile),
        "--chapters",
        "5-17",
        "--language",
        "en-GB",
        "--diagnostics",
        "all",
        "--speech",
        "2-9",
        "--renderplan-root",
        str(tmp_path / "renderplans"),
        "--voicerender-output-root",
        str(tmp_path / "voice-output"),
        "--cache-dir",
        str(tmp_path / "cache"),
        "--reset-stale",
        "--render-emphasis-policy",
        "reject",
        "--compose-renderplan-root",
        str(tmp_path / "compose-renderplans"),
        "--compose-voicerender-root",
        str(tmp_path / "compose-voice"),
        "--compose-output-root",
        str(tmp_path / "compose-output"),
        "--media-root",
        str(tmp_path / "media"),
        "--save-audiojobs",
        str(tmp_path / "audiojobs"),
        "--compose-sample-rate",
        "22050",
        "--compose-channels",
        "1",
        "--clip-policy",
        "warn",
        "--target-lufs",
        "-16",
        "--true-peak-ceiling-dbtp",
        "-2",
        "--peak-policy",
        "error",
        "--compose-emphasis-policy",
        "strict",
        "--output",
        str(tmp_path / "book.m4b"),
        "--export-profile",
        str(tmp_path / "export.toml"),
        "--cover",
        str(tmp_path / "cover.jpg"),
        "--bitrate",
        "128k",
        "--metadata",
        "title=Story",
        "--metadata",
        "author=Reader",
        "--export-sample-rate",
        "44100",
        "--export-channels",
        "1",
        "--ffmpeg",
        str(tmp_path / "ffmpeg"),
        "--ffprobe",
        str(tmp_path / "ffprobe"),
        "--force",
        "--fail-fast",
        "--stop-after",
        "synth",
        "--json",
    ]

    assert cli.main(args) == 0

    request = captured["request"]
    assert request.workspace == workspace
    assert request.profile == profile
    assert request.chapters == "5-17"
    assert request.compile_request.book == workspace
    assert request.compile_request.language == "en-GB"
    assert request.compile_request.diagnostics == "all"
    assert request.render_request.profile == profile
    assert request.render_request.chapters == "5-17"
    assert request.render_request.variant is None
    assert request.speech == "2-9"
    assert request.renderplan_root == tmp_path / "renderplans"
    assert request.voicerender_output_root == tmp_path / "voice-output"
    assert request.cache_dir == tmp_path / "cache"
    assert request.reset_stale_output is True
    assert request.render_emphasis_policy == "reject"
    assert request.compose_options.sample_rate == 22050
    assert request.compose_options.channels == 1
    assert request.compose_options.clip_policy == "warn"
    assert request.compose_options.target_lufs == -16.0
    assert request.compose_options.true_peak_ceiling_dbtp == -2.0
    assert request.compose_options.peak_policy == "error"
    assert request.compose_renderplan_root == tmp_path / "compose-renderplans"
    assert request.compose_voicerender_root == tmp_path / "compose-voice"
    assert request.compose_output_root == tmp_path / "compose-output"
    assert request.media_root == tmp_path / "media"
    assert request.save_audiojobs == tmp_path / "audiojobs"
    assert request.compose_emphasis_policy == "strict"
    assert request.output == tmp_path / "book.m4b"
    assert request.export_profile == tmp_path / "export.toml"
    assert request.export_metadata == {"title": "Story", "author": "Reader"}
    assert request.cover == tmp_path / "cover.jpg"
    assert request.bitrate == "128k"
    assert request.export_sample_rate == 44100
    assert request.export_channels == 1
    assert request.ffmpeg == tmp_path / "ffmpeg"
    assert request.ffprobe == tmp_path / "ffprobe"
    assert request.force is True
    assert request.fail_fast is True
    assert request.stop_after == "synth"

    captured_output = capsys.readouterr()
    assert json.loads(captured_output.out) == {
        "workspace": str(workspace),
        "variant": "exact-variant",
        "completed_stages": ["compile"],
    }
    assert "synth: progress 4/7 rendering" in captured_output.err


def test_audiobook_convert_cli_forwards_conversion_controls(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    source = tmp_path / "novel.epub"
    workspace = tmp_path / "novel.ssmdbook"
    captured: dict[str, object] = {}

    def convert(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return type("Workspace", (), {"path": workspace})()

    monkeypatch.setattr(cli.api, "convert_book", convert)
    assert (
        cli.main(
            [
                "audiobook",
                "convert",
                str(source),
                "--output",
                str(workspace),
                "--chapters",
                "5-17",
                "--lang",
                "en-US",
                "--metadata",
                "title=Novel",
                "--sequence-fallback-mode",
                "preserve",
                "--speech-mode",
                "preserve",
                "--spacy",
                "auto",
                "--json",
                "--no-progress",
            ]
        )
        == 0
    )

    assert captured["args"] == (source, workspace)
    assert captured["kwargs"] == {
        "chapters": "5-17",
        "language": "en-US",
        "metadata": {"title": "Novel"},
        "sequence_fallback_mode": "preserve",
        "speech_mode": "preserve",
        "spacy": "auto",
        "on_progress": None,
    }
    assert json.loads(capsys.readouterr().out) == {"workspace": str(workspace)}
