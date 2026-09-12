from pathlib import Path

import pytest

from readio.config import (
    PathSettings,
    ReaderConfig,
    ReadioConfig,
    dumps_config,
    load_config,
    set_config_value,
    validate_config,
    voice_role,
)


def test_config_round_trip(tmp_path: Path):
    path = tmp_path / "config.toml"
    cfg = ReaderConfig(
        voice="bf_emma",
        lang="en-gb",
        speed=1.25,
        unit="paragraph",
        device="USB",
        spacy="off",
    )
    path.write_text(dumps_config(cfg), encoding="utf-8")
    assert load_config(path) == cfg


def test_set_config_coerces_values():
    cfg = ReaderConfig()
    assert set_config_value(cfg, "speed", "1.4").speed == 1.4
    assert set_config_value(cfg, "queue_size", "4").queue_size == 4


def test_invalid_unit_rejected():
    with pytest.raises(ValueError):
        set_config_value(ReaderConfig(), "unit", "word")


def test_default_config_has_provider_and_analyst_role():
    cfg = ReadioConfig()
    assert cfg.ssmd.voice_provider == "kokoro"
    assert voice_role(cfg, "analyst") in cfg.voices["kokoro"].ids


def test_config_round_trip_nested_sections(tmp_path: Path):
    path = tmp_path / "config.toml"
    cfg = ReadioConfig(
        paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "output")
    )
    path.write_text(dumps_config(cfg), encoding="utf-8")
    loaded = load_config(path)
    assert loaded == cfg


def test_legacy_reader_only_config_loads(tmp_path: Path):
    path = tmp_path / "legacy.toml"
    path.write_text('[reader]\nvoice = "bf_emma"\nlang = "en-gb"\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.schema == 0
    assert cfg.reader.voice == "bf_emma"
    assert cfg.ssmd.voice_provider == "kokoro"


def test_dotted_config_set_role_and_invalid_target():
    cfg = set_config_value(ReadioConfig(), "voices.kokoro.roles.analyst", "am_adam")
    assert cfg.voices["kokoro"].roles["analyst"] == "am_adam"
    with pytest.raises(ValueError, match="not present"):
        validate_config(set_config_value(ReadioConfig(), "voices.kokoro.roles.analyst", "missing"))


def test_reader_policy_defaults() -> None:
    cfg = ReaderConfig()
    assert cfg.spacy == "auto"
    assert cfg.short_sentence == "auto"


def test_reader_policies_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "policies.toml"
    for spacy in ("auto", "off", "sm", "md", "lg", "trf"):
        for short_sentence in ("auto", "off", "wrap", "phrase", "randomized-phrase"):
            cfg = ReaderConfig(spacy=spacy, short_sentence=short_sentence)
            path.write_text(dumps_config(cfg), encoding="utf-8")
            assert load_config(path).reader == cfg


def test_legacy_required_spacy_migrates_to_sm(tmp_path: Path) -> None:
    path = tmp_path / "legacy.toml"
    path.write_text('[reader]\nspacy = "required"\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.reader.spacy == "sm"
    dumped = dumps_config(cfg)
    assert 'spacy = "sm"' in dumped
    assert "required" not in dumped


def test_invalid_reader_policies_rejected() -> None:
    with pytest.raises(ValueError):
        set_config_value(ReaderConfig(), "spacy", "xl")
    with pytest.raises(ValueError):
        set_config_value(ReaderConfig(), "short_sentence", "fast")
