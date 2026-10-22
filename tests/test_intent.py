from __future__ import annotations

from pathlib import Path

import pytest

from readio.intent import (
    INTENT_SCHEMA,
    create_book_intent,
    create_document_intent,
    validate_render_intent,
)
from readio.intent_codec import (
    dump_render_intent,
    load_render_intent,
    parse_render_intent,
    save_render_intent,
)


def test_intent_creators_apply_book_and_document_defaults() -> None:
    book = create_book_intent("novel.epub", profile="cast.renderprofile.toml")
    document = create_document_intent("article.ssmd.md", profile="narration.renderprofile.toml")

    assert book.kind == "book"
    assert book.input.source == Path("novel.epub")
    assert book.input.bundle == Path("novel.ssmd-bundle")
    assert book.input.chapters == "all"
    assert book.voice.profile == Path("cast.renderprofile.toml")
    assert book.output.format == "m4b"
    assert book.output.path == Path("novel.m4b")
    assert book.output.bitrate == "96k"
    assert book.planning.diagnostics == "errors"
    assert book.render.speech == "all"
    assert book.compose.sample_rate == 24000

    assert document.kind == "document"
    assert document.input.bundle == Path("article.ssmd-bundle")
    assert document.input.chapters is None
    assert document.output.format == "mp3"
    assert document.output.path == Path("article.mp3")


def test_intent_creation_is_in_memory_only() -> None:
    book = create_book_intent("missing.epub", profile="missing.renderprofile.toml")
    assert book.output.path == Path("missing.m4b")


def test_full_intent_toml_round_trips_all_stable_values(tmp_path: Path) -> None:
    intent = create_book_intent(
        "A Novel.epub",
        profile="cast.renderprofile.toml",
        chapters="5-17",
        language="en-US",
        variant="preview-chapter-5",
        diagnostics="all",
        text_preparation="ssmd",
        overlap_mode="strict",
        renderability_mode="strict",
        spacy="off",
        spacy_model="en_core_web_sm",
        spacy_model_size="sm",
        require_spacy=True,
        speech="selected",
        emphasis_policy="reject",
        compose_emphasis_policy="strict",
        compose_sample_rate=48000,
        target_lufs=-16.5,
        true_peak_ceiling_dbtp=-1.0,
        clip_policy="warn",
        peak_policy="reduce_gain",
        output="A Novel-preview.m4b",
        bitrate="128k",
        export_sample_rate=48000,
        channels=2,
        cover="cover.jpg",
        timeline="timeline.json",
        metadata={"title": 'A "Novel"', "author": "Author"},
    )

    rendered = dump_render_intent(intent)
    path = tmp_path / "intent.readio.toml"
    path.write_text(rendered, encoding="utf-8")
    decoded = load_render_intent(path)

    assert decoded == intent
    assert 'schema = "readio.render.v1"' in rendered
    assert 'kind = "book"' in rendered
    assert "[output.metadata]" in rendered
    assert '"title" = "A \\"Novel\\""' in rendered


def test_minimal_intent_omits_effective_defaults() -> None:
    intent = create_book_intent("novel.epub", profile="cast.renderprofile.toml")
    rendered = dump_render_intent(intent, minimal=True)

    assert 'schema = "readio.render.v1"' in rendered
    assert 'kind = "book"' in rendered
    assert "[input]" in rendered
    assert 'source = "novel.epub"' in rendered
    assert "[voice]" in rendered
    assert 'profile = "cast.renderprofile.toml"' in rendered
    assert "novel.ssmd-bundle" not in rendered
    assert "chapters" not in rendered
    assert "[planning]" not in rendered
    assert "[render]" not in rendered
    assert "[compose]" not in rendered
    assert "[output]" not in rendered


def test_minimal_toml_omissions_load_as_schema_defaults(tmp_path: Path) -> None:
    intent = create_book_intent("novel.epub", profile="cast.renderprofile.toml")
    path = tmp_path / "minimal.readio.toml"
    path.write_text(dump_render_intent(intent, minimal=True), encoding="utf-8")
    parsed = load_render_intent(path)

    assert parsed.kind == "book"
    assert parsed.input.chapters is None
    assert parsed.planning.diagnostics == "errors"
    assert parsed.compose.target_lufs == -18.0
    assert parsed.output.format == "auto"
    assert parsed.output.path is None


def test_parse_rejects_unknown_schema_and_unknown_keys() -> None:
    with pytest.raises(ValueError, match="unsupported schema"):
        parse_render_intent({"schema": "readio.render.v2"})

    with pytest.raises(ValueError, match="unknown top-level"):
        parse_render_intent({"schema": INTENT_SCHEMA, "rendering": {}})

    with pytest.raises(ValueError, match="unknown key.*input"):
        parse_render_intent(
            {"schema": INTENT_SCHEMA, "input": {"source": "book.epub", "unknown": True}}
        )


def test_parse_rejects_invalid_field_types_and_values() -> None:
    with pytest.raises(ValueError, match="planning.diagnostics"):
        parse_render_intent({"schema": INTENT_SCHEMA, "planning": {"diagnostics": "quiet"}})

    with pytest.raises(ValueError, match="chapters"):
        parse_render_intent(
            {
                "schema": INTENT_SCHEMA,
                "kind": "book",
                "input": {"chapters": "0-3"},
            }
        )

    with pytest.raises(ValueError, match="require_spacy"):
        parse_render_intent({"schema": INTENT_SCHEMA, "planning": {"require_spacy": "false"}})

    with pytest.raises(ValueError, match="metadata"):
        parse_render_intent(
            {
                "schema": INTENT_SCHEMA,
                "output": {"metadata": {"title": 1}},
            }
        )


def test_document_intent_rejects_book_only_selection() -> None:
    with pytest.raises(ValueError, match="not valid for document"):
        parse_render_intent(
            {
                "schema": INTENT_SCHEMA,
                "kind": "document",
                "input": {"chapters": "5"},
            }
        )


def test_intent_save_load_and_overwrite_policy(tmp_path: Path) -> None:
    intent = create_document_intent("article.ssmd.md", profile="narration.renderprofile.toml")
    path = tmp_path / "article.readio.toml"

    assert save_render_intent(intent, path) == path
    assert load_render_intent(path) == intent
    assert not list(tmp_path.glob(".*.tmp"))
    with pytest.raises(FileExistsError, match="already exists"):
        save_render_intent(intent, path)

    replacement = create_document_intent(
        "new-article.ssmd.md", profile="narration.renderprofile.toml"
    )
    save_render_intent(replacement, path, force=True)
    assert load_render_intent(path) == replacement


def test_save_refuses_symlink_target(tmp_path: Path) -> None:
    target = tmp_path / "real.toml"
    target.write_text("keep", encoding="utf-8")
    link = tmp_path / "intent.toml"
    link.symlink_to(target)
    intent = create_document_intent("article.md", profile="profile.toml")

    with pytest.raises(ValueError, match="symbolic-link"):
        save_render_intent(intent, link, force=True)
    assert target.read_text(encoding="utf-8") == "keep"


def test_validate_intent_resolves_source_and_profile_from_config_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "book.epub").write_text("source", encoding="utf-8")
    (config_dir / "cast.renderprofile.toml").write_text("profile", encoding="utf-8")
    validated: list[Path] = []

    def validate_profile(path: str | Path) -> object:
        validated.append(Path(path))
        return object()

    monkeypatch.setattr("readio.profiles.validate_profile", validate_profile)
    intent = create_book_intent("book.epub", profile="cast.renderprofile.toml")

    validate_render_intent(intent, config_path=config_dir / "book.readio.toml")
    assert validated == [config_dir / "cast.renderprofile.toml"]


def test_validate_intent_rejects_missing_source_and_output_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "profile.toml"
    profile.write_text("profile", encoding="utf-8")
    monkeypatch.setattr("readio.profiles.validate_profile", lambda _path: object())

    missing = create_book_intent("missing.epub", profile=profile)
    with pytest.raises(FileNotFoundError, match="source does not exist"):
        validate_render_intent(missing)

    source = tmp_path / "book.epub"
    source.write_text("source", encoding="utf-8")
    wrong_extension = create_book_intent(source, profile=profile, output="book.mp3")
    with pytest.raises(ValueError, match="extension must match"):
        validate_render_intent(wrong_extension)
