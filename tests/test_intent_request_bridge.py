from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from readio.api import Readio, RenderRequest, render_request_from_intent
from readio.intent import create_book_intent, create_document_intent


def _configure_resolver(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    formats = {name: SimpleNamespace(extension=f".{name}") for name in ("m4b", "mp3")}
    monkeypatch.setattr("readio.intent_resolver.supported_formats", lambda: formats)
    monkeypatch.setattr("readio.intent_resolver.inspect_source_kind", lambda _source: kind)
    monkeypatch.setattr("readio.profiles.validate_profile", lambda _path: object())


def test_intent_bridge_resolves_paths_and_maps_all_option_groups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / "project"
    config_dir.mkdir()
    source = config_dir / "novel.epub"
    source.write_bytes(b"epub")
    (config_dir / "narrator.renderprofile.toml").write_text("profile", encoding="utf-8")
    (config_dir / "cover.jpg").write_bytes(b"cover")
    (config_dir / "timeline.json").write_text("{}", encoding="utf-8")
    _configure_resolver(monkeypatch, "book")

    intent = create_book_intent(
        "novel.epub",
        profile="narrator.renderprofile.toml",
        bundle="novel.ssmd-bundle",
        chapters="5-17",
        language="en-US",
        variant="narrator--primary",
        diagnostics="warnings",
        text_preparation="ssmd",
        overlap_mode="strict",
        renderability_mode="strict",
        spacy="on",
        spacy_model="en_core_web_sm",
        spacy_model_size="sm",
        require_spacy=True,
        speech="dialogue",
        emphasis_policy="reject",
        compose_sample_rate=44100,
        target_lufs=-16.0,
        true_peak_ceiling_dbtp=-2.0,
        clip_policy="warn",
        peak_policy="reduce_gain",
        output_format="m4b",
        output="finished.m4b",
        bitrate="128k",
        export_sample_rate=48000,
        channels=2,
        cover="cover.jpg",
        timeline="timeline.json",
        metadata={"title": "A Novel"},
    )

    request = Readio().request_from_intent(
        intent,
        config_path=config_dir / "novel.readio.toml",
        rebuild_from="render_plan",
        replace_output=True,
        ffmpeg=Path("/usr/bin/ffmpeg"),
        ffprobe=Path("/usr/bin/ffprobe"),
    )

    assert isinstance(request, RenderRequest)
    assert request.source == source.resolve()
    assert request.profile == (config_dir / "narrator.renderprofile.toml").resolve()
    assert request.bundle == (config_dir / "novel.ssmd-bundle").resolve()
    assert request.chapters == "5-17"
    assert request.language == "en-US"
    assert request.variant == "narrator--primary"
    assert request.output == (config_dir / "finished.m4b").resolve()
    assert request.format == "m4b"
    assert request.bitrate == "128k"
    assert request.sample_rate == 48000
    assert request.channels == 2
    assert request.rebuild_from == "render_plan"
    assert request.replace_output is True

    assert request.planning.diagnostics == "warnings"
    assert request.planning.planner_config.language == "en-US"
    assert request.planning.planner_config.text_preparation == "ssmd"
    assert request.planning.planner_config.overlap_mode == "strict"
    assert request.planning.planner_config.renderability_mode == "strict"
    assert request.planning.planner_config.linguistics.use_spacy is True
    assert request.planning.planner_config.linguistics.spacy_model == "en_core_web_sm"
    assert request.planning.planner_config.linguistics.spacy_model_size == "sm"
    assert request.planning.planner_config.linguistics.require_spacy is True

    assert request.voice_render.speech == "dialogue"
    assert request.voice_render.emphasis_policy == "reject"
    assert request.composition.emphasis_policy == "segment_fallback"
    assert request.composition.options.sample_rate == 44100
    assert request.composition.options.target_lufs == -16.0
    assert request.composition.options.true_peak_ceiling_dbtp == -2.0
    assert request.composition.options.clip_policy == "warn"
    assert request.composition.options.peak_policy == "reduce_gain"

    assert request.export.metadata == {"title": "A Novel"}
    assert request.export.cover == (config_dir / "cover.jpg").resolve()
    assert request.export.timeline == (config_dir / "timeline.json").resolve()
    assert request.export.ffmpeg == Path("/usr/bin/ffmpeg")
    assert request.export.ffprobe == Path("/usr/bin/ffprobe")


def test_intent_bridge_defaults_to_temporary_workspace_and_public_function(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    source = config_dir / "article.md"
    source.write_text("A short article.", encoding="utf-8")
    (config_dir / "profile.renderprofile.toml").write_text("profile", encoding="utf-8")
    _configure_resolver(monkeypatch, "document")
    intent = create_document_intent("article.md", profile="profile.renderprofile.toml")

    request = render_request_from_intent(
        intent,
        config_path=config_dir / "article.readio.toml",
    )

    assert request.source == source.resolve()
    assert request.bundle is None
    assert request.output == (config_dir / "article.mp3").resolve()
    assert request.format == "mp3"
