from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from readio.intent import create_book_intent, create_document_intent
from readio.intent_resolver import resolve_render_intent
from readio.workspace import WorkspaceRef


@dataclass(frozen=True)
class _Format:
    extension: str


def _formats(*names: str) -> dict[str, _Format]:
    return {name: _Format(f".{name}") for name in names}


def _mock_profile_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("readio.profiles.validate_profile", lambda _path: object())


def test_resolver_uses_config_directory_for_intent_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    source = config_dir / "story.md"
    source.write_text("A short story.", encoding="utf-8")
    profile = config_dir / "cast.renderprofile.toml"
    profile.write_text("profile", encoding="utf-8")
    monkeypatch.setattr("readio.intent_resolver.supported_formats", lambda: _formats("mp3"))
    _mock_profile_validation(monkeypatch)

    intent = create_document_intent("story.md", profile="cast.renderprofile.toml")
    resolved = resolve_render_intent(
        intent,
        config_path=config_dir / "story.readio.toml",
        check_output_collision=False,
    )

    assert resolved.config_path == (config_dir / "story.readio.toml").resolve()
    assert resolved.source == source.resolve()
    assert resolved.bundle == (config_dir / "story.ssmd-bundle").resolve()
    assert resolved.profile == profile.resolve()
    assert resolved.kind == "document"
    assert resolved.output_format == "mp3"
    assert resolved.output == (config_dir / "story.mp3").resolve()
    assert resolved.intent.input.source == resolved.source
    assert resolved.intent.input.bundle == resolved.bundle


def test_resolver_applies_cli_overrides_to_config_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for name in ("configured.md", "override.md"):
        (tmp_path / name).write_text("source", encoding="utf-8")
    for name in ("configured.renderprofile.toml", "override.renderprofile.toml"):
        (tmp_path / name).write_text("profile", encoding="utf-8")
    monkeypatch.setattr("readio.intent_resolver.supported_formats", lambda: _formats("wav", "mp3"))
    monkeypatch.setattr("readio.intent_resolver.inspect_source_kind", lambda _source: "document")
    _mock_profile_validation(monkeypatch)

    intent = create_document_intent(
        "configured.md",
        profile="configured.renderprofile.toml",
        language="en-GB",
    )
    resolved = resolve_render_intent(
        intent,
        config_path=config_dir / "intent.readio.toml",
        source="override.md",
        bundle="override.ssmd-bundle",
        profile="override.renderprofile.toml",
        variant="override-label",
        language="fr-FR",
        output_format="wav",
        output="custom.wav",
        check_output_collision=False,
    )

    assert resolved.source == (tmp_path / "override.md").resolve()
    assert resolved.bundle == (tmp_path / "override.ssmd-bundle").resolve()
    assert resolved.profile == (tmp_path / "override.renderprofile.toml").resolve()
    assert resolved.language == "fr-FR"
    assert resolved.output_format == "wav"
    assert resolved.output == (tmp_path / "custom.wav").resolve()
    assert resolved.intent.input.source == resolved.source
    assert resolved.intent.voice.profile == resolved.profile
    assert resolved.intent.voice.variant == "override-label"


def test_resolver_reuses_existing_bundle_and_checks_declared_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "story.ssmd-bundle"
    bundle.mkdir()
    profile = tmp_path / "profile.renderprofile.toml"
    profile.write_text("profile", encoding="utf-8")
    monkeypatch.setattr("readio.intent_resolver.supported_formats", lambda: _formats("m4b", "mp3"))
    monkeypatch.setattr(
        "readio.intent_resolver.resolve_workspace",
        lambda path: WorkspaceRef(Path(path), "book"),
    )
    _mock_profile_validation(monkeypatch)

    intent = create_book_intent(bundle, profile=profile)
    resolved = resolve_render_intent(intent, check_output_collision=False)
    assert resolved.bundle == bundle.resolve()
    assert resolved.kind == "book"
    assert resolved.output_format == "m4b"
    assert resolved.output == (tmp_path / "story.m4b").resolve()

    mismatched = create_document_intent(bundle, profile=profile)
    with pytest.raises(ValueError, match="does not match SSMDConvert kind"):
        resolve_render_intent(mismatched, check_output_collision=False)


def test_resolver_checks_authoritative_format_and_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "story.md"
    source.write_text("source", encoding="utf-8")
    profile = tmp_path / "profile.toml"
    profile.write_text("profile", encoding="utf-8")
    monkeypatch.setattr("readio.intent_resolver.supported_formats", lambda: _formats("mp3", "wav"))
    monkeypatch.setattr("readio.intent_resolver.inspect_source_kind", lambda _source: "document")
    _mock_profile_validation(monkeypatch)
    intent = create_document_intent(source, profile=profile)

    with pytest.raises(ValueError, match="does not support output format"):
        resolve_render_intent(intent, output_format="aiff", check_output_collision=False)
    with pytest.raises(ValueError, match="extension must be .wav"):
        resolve_render_intent(
            intent,
            output_format="wav",
            output=tmp_path / "wrong.mp3",
            check_output_collision=False,
        )


def test_resolver_rejects_existing_output_without_explicit_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "story.md"
    source.write_text("source", encoding="utf-8")
    profile = tmp_path / "profile.toml"
    profile.write_text("profile", encoding="utf-8")
    output = tmp_path / "story.mp3"
    output.write_bytes(b"existing")
    monkeypatch.setattr("readio.intent_resolver.supported_formats", lambda: _formats("mp3"))
    monkeypatch.setattr("readio.intent_resolver.inspect_source_kind", lambda _source: "document")
    _mock_profile_validation(monkeypatch)
    intent = create_document_intent(source, profile=profile)

    with pytest.raises(FileExistsError, match="output already exists"):
        resolve_render_intent(intent)
    resolved = resolve_render_intent(intent, force_output=True)
    assert resolved.output == output.resolve()


def test_resolver_rejects_invalid_bundle_path_and_document_chapter_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "story.md"
    source.write_text("source", encoding="utf-8")
    profile = tmp_path / "profile.toml"
    profile.write_text("profile", encoding="utf-8")
    monkeypatch.setattr("readio.intent_resolver.supported_formats", lambda: _formats("mp3"))
    monkeypatch.setattr("readio.intent_resolver.inspect_source_kind", lambda _source: "document")
    _mock_profile_validation(monkeypatch)

    invalid_bundle = create_document_intent(source, profile=profile, bundle=tmp_path / "bundle")
    with pytest.raises(ValueError, match="must end in .ssmd-bundle"):
        resolve_render_intent(invalid_bundle, check_output_collision=False)

    valid = create_document_intent(source, profile=profile)
    with pytest.raises(ValueError, match="not valid for document"):
        resolve_render_intent(valid, chapters="5", check_output_collision=False)
