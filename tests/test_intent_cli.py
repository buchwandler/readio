from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from readio import cli
from readio.intent import create_book_intent, create_document_intent


def test_book_create_stdout_is_reviewable_toml_without_service_or_rendering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def unexpected_service() -> Any:
        raise AssertionError("intent creation must not instantiate the render service")

    monkeypatch.setattr(cli.api, "Readio", unexpected_service)
    status = cli.main(
        [
            "intent",
            "book",
            "create",
            "novel.epub",
            "--profile",
            "cast.renderprofile.toml",
            "--stdout",
        ]
    )

    assert status == 0
    content = capsys.readouterr().out
    assert 'schema = "readio.render.v1"' in content
    assert 'kind = "book"' in content
    assert 'source = "novel.epub"' in content
    assert 'chapters = "all"' in content
    assert 'profile = "cast.renderprofile.toml"' in content
    assert 'format = "m4b"' in content
    assert 'path = "novel.m4b"' in content
    assert not (tmp_path / "novel.readio.toml").exists()


def test_document_create_uses_authoritative_formats_and_default_intent_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "supported_formats",
        lambda: {
            "mp3": SimpleNamespace(extension=".mp3"),
            "flac": SimpleNamespace(extension=".flac"),
        },
    )

    assert (
        cli.main(
            [
                "intent",
                "document",
                "create",
                "article.ssmd.md",
                "--profile",
                "narrator.renderprofile.toml",
                "--format",
                "flac",
                "--title",
                "Article title",
                "--metadata",
                "custom=value",
            ]
        )
        == 0
    )

    config = tmp_path / "article.readio.toml"
    assert config.is_file()
    intent = cli.api.load_render_intent(config)
    assert intent.kind == "document"
    assert intent.input.bundle == Path("article.ssmd-bundle")
    assert intent.input.chapters is None
    assert intent.output.format == "flac"
    assert intent.output.path == Path("article.flac")
    assert intent.output.metadata == {"title": "Article title", "custom": "value"}
    assert "Created render intent: article.readio.toml" in capsys.readouterr().out
    assert not (tmp_path / ".readio").exists()


def test_document_create_without_profile_reports_detected_roles_and_next_action(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "cast.ssmd.md"
    source.write_text(
        ':::{voice="host"}\nHost line.\n:::\n\n:::{voice="narrator"}\nNarration.\n:::\n',
        encoding="utf-8",
    )

    assert cli.main(["intent", "document", "create", str(source)]) == 2
    human = capsys.readouterr().err
    assert "this render intent needs a VoiceRender profile" in human
    assert "Detected document voice roles:" in human
    assert "  narrator" in human and "  host" in human
    assert "readio profile document create" in human
    assert "narrator=<VOICE>" in human
    assert "readio intent document create" in human
    assert not (tmp_path / "cast.readio.toml").exists()
    assert not (tmp_path / "cast.renderprofile.toml").exists()

    assert cli.main(["intent", "document", "create", str(source), "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "profile_required"
    assert error["source_roles"] == ["narrator", "host"]
    assert error["next_action"][:4] == ["readio", "profile", "document", "create"]
    assert "--voice" in error["next_action"]
    assert not (tmp_path / "cast.readio.toml").exists()
    assert not (tmp_path / "cast.renderprofile.toml").exists()


def test_book_create_without_profile_has_structured_generic_next_action(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["intent", "book", "create", "novel.epub", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "profile_required"
    assert error["source_roles"] == []
    assert error["next_action"][:3] == ["readio", "profile", "create"]
    assert not (tmp_path / "novel.readio.toml").exists()


def test_creator_force_and_metadata_precedence_are_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "book.readio.toml"
    create_args = [
        "intent",
        "book",
        "create",
        "book.epub",
        "--profile",
        "profile.toml",
        "--metadata",
        "title=generic",
        "--metadata",
        "custom=first",
        "--title",
        "convenience",
        "-o",
        str(config),
    ]

    assert cli.main(create_args) == 0
    intent = cli.api.load_render_intent(config)
    assert intent.output.metadata == {"title": "convenience", "custom": "first"}
    original = config.read_text(encoding="utf-8")
    assert cli.main(create_args) == 1
    assert config.read_text(encoding="utf-8") == original
    assert cli.main([*create_args, "--force"]) == 0

    duplicate = [
        "intent",
        "document",
        "create",
        "article.md",
        "--profile",
        "profile.toml",
        "--metadata",
        "tag=one",
        "--metadata",
        "tag=two",
        "--stdout",
    ]
    monkeypatch.setattr(
        cli, "supported_formats", lambda: {"mp3": SimpleNamespace(extension=".mp3")}
    )
    assert cli.main(duplicate) == 2
    assert "supplied more than once" in capsys.readouterr().err


def test_show_supports_human_normalized_toml_and_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "book.readio.toml"
    intent = create_book_intent("book.epub", profile="profile.toml", chapters="5-7")
    cli.api.save_render_intent(intent, path)

    assert cli.main(["intent", "show", str(path)]) == 0
    human = capsys.readouterr().out
    assert "Intent: " in human
    assert "chapters     5-7" in human
    assert "variant      <automatic>" in human

    assert cli.main(["intent", "show", str(path), "--toml"]) == 0
    normalized_toml = capsys.readouterr().out
    assert normalized_toml == cli.api.dump_render_intent(intent)

    assert cli.main(["intent", "show", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"]["voice"]["profile"] == "profile.toml"
    assert payload["result"]["output"]["metadata"] == {}


def test_validate_resolves_without_rendering_and_emits_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "document.readio.toml"
    intent = create_document_intent("article.md", profile="profile.toml")
    cli.api.save_render_intent(intent, path)
    captured: dict[str, Any] = {}

    def fake_resolve(received: Any, **kwargs: Any) -> SimpleNamespace:
        captured["intent"] = received
        captured.update(kwargs)
        return SimpleNamespace(kind="document", output=Path("article.mp3"))

    monkeypatch.setattr(cli, "resolve_render_intent", fake_resolve)
    assert cli.main(["intent", "validate", str(path), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"]["valid"] is True
    assert payload["result"]["resolved"]["kind"] == "document"
    assert captured["config_path"] == path
    assert captured["check_output_collision"] is False


def test_creator_help_has_complete_kind_specific_options() -> None:
    parser = cli.build_parser()
    help_text = parser.format_help()
    assert "intent" in help_text

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "intent",
                "document",
                "create",
                "article.md",
                "--profile",
                "profile.toml",
                "--chapters",
                "1",
            ]
        )

    book_args = parser.parse_args(
        [
            "intent",
            "book",
            "create",
            "book.epub",
            "--profile",
            "profile.toml",
            "--chapters",
            "5-10",
            "--no-require-spacy",
            "--compose-sample-rate",
            "48000",
            "--series-index",
            "2",
        ]
    )
    assert book_args.chapters == "5-10"
    assert book_args.require_spacy is False
    assert book_args.compose_sample_rate == 48000
    assert book_args.series_index == "2"
