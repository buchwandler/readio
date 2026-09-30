from pathlib import Path

import pytest

from readio.config import (
    PathSettings,
    ReaderConfig,
    ReadioConfig,
    VoiceProviderSettings,
    dumps_config,
    load_config,
    role_targets,
    set_config_value,
    validate_config,
    voice_role,
)
from readio.role_targets import VoiceTarget


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
    assert cfg.pause_mode == "auto"
    assert cfg.spacy == "auto"
    assert cfg.short_sentence == "phrase"


def test_reader_voice_level_and_finite_speed_are_validated(tmp_path: Path) -> None:
    cfg = ReaderConfig(voice_level="calibrated")
    path = tmp_path / "voice-level.toml"
    path.write_text(dumps_config(cfg), encoding="utf-8")
    assert load_config(path).reader.voice_level == "calibrated"
    assert set_config_value(ReaderConfig(), "voice_level", "calibrated").voice_level == "calibrated"
    with pytest.raises(ValueError, match="reader.voice_level"):
        set_config_value(ReaderConfig(), "voice_level", "unknown")
    with pytest.raises(ValueError, match="finite"):
        set_config_value(ReaderConfig(), "speed", float("nan"))


def test_reader_policies_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "policies.toml"
    for spacy in ("auto", "off", "sm", "md", "lg", "trf"):
        for short_sentence in ("off", "wrap", "phrase", "randomized-phrase"):
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


def test_legacy_short_sentence_auto_loads_as_phrase(tmp_path: Path) -> None:
    path = tmp_path / "legacy.toml"
    path.write_text('[reader]\nshort_sentence = "auto"\n', encoding="utf-8")

    cfg = load_config(path)

    assert cfg.reader.short_sentence == "phrase"
    dumped = dumps_config(cfg)
    assert 'short_sentence = "phrase"' in dumped
    assert 'short_sentence = "auto"' not in dumped


def test_invalid_reader_policies_rejected() -> None:
    with pytest.raises(ValueError):
        set_config_value(ReaderConfig(), "spacy", "xl")
    with pytest.raises(ValueError):
        set_config_value(ReaderConfig(), "short_sentence", "fast")

    with pytest.raises(ValueError, match="reader.short_sentence"):
        set_config_value(ReaderConfig(), "short_sentence", "auto")


def test_role_target_config_round_trip_and_legacy_compatibility(tmp_path: Path) -> None:
    target = VoiceTarget(
        engine="pipersynth",
        voice="en_US-amy-medium",
        target_id="amy-asset",
        selector="en-pi-13",
    )
    cfg = ReadioConfig(roles={"guest": target})
    path = tmp_path / "roles.toml"
    path.write_text(dumps_config(cfg), encoding="utf-8")

    loaded = load_config(path)

    assert loaded.roles["guest"] == VoiceTarget(
        engine="piper",
        voice="en_US-amy-medium",
        target_id="amy-asset",
        selector="en-pi-13",
    )
    assert role_targets(loaded)["guest"] == loaded.roles["guest"]
    assert role_targets(loaded, provider="piper")["guest"] == loaded.roles["guest"]
    assert 'engine = "piper"' in path.read_text(encoding="utf-8")


def test_conflicting_legacy_global_roles_are_ambiguous() -> None:
    cfg = ReadioConfig(
        voices={
            "kokoro": VoiceProviderSettings(ids=("af_sarah",), roles={"guest": "af_sarah"}),
            "piper": VoiceProviderSettings(
                ids=("en_US-amy-medium",), roles={"guest": "en_US-amy-medium"}
            ),
        }
    )

    with pytest.raises(ValueError, match="ambiguous across legacy providers"):
        role_targets(cfg)
