from __future__ import annotations

from pathlib import Path

import pytest

from readio.config import (
    PathSettings,
    ReaderSettings,
    ReadioConfig,
    dumps_config,
    load_config,
    role_targets,
    save_config,
    set_config_value,
)
from readio.migrations import MigrationError, migrate_config_data, migrate_config_file
from readio.role_targets import VoiceTarget


def test_config_round_trip_schema_3_is_engine_neutral(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    cfg = ReadioConfig(
        reader=ReaderSettings(
            voice="bf_emma",
            lang="en-gb",
            speed=1.25,
            unit="paragraph",
            device="USB",
            spacy="off",
        ),
        paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "output"),
        roles={"guest": VoiceTarget("pipersynth", "en_US-amy-medium", "amy-asset")},
    )
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded == cfg
    assert loaded.schema == 3
    serialized = path.read_text(encoding="utf-8")
    assert "schema = 3" in serialized
    assert "[voices." not in serialized
    assert "voice_provider" not in serialized
    assert 'engine = "piper"' in serialized


def test_default_config_uses_engine_defaults_not_a_global_voice() -> None:
    cfg = ReadioConfig()
    assert cfg.schema == 3
    assert cfg.reader.engine == "kokoro"
    assert cfg.reader.voice is None
    assert cfg.roles["analyst"] == VoiceTarget("kokoro", "am_michael")
    assert role_targets(cfg)["guest"] == VoiceTarget("kokoro", "af_bella")


def test_set_config_coerces_values_and_updates_structured_roles() -> None:
    cfg = set_config_value(ReadioConfig(), "reader.speed", "1.4")
    assert cfg.reader.speed == 1.4
    cfg = set_config_value(cfg, "roles.analyst.voice", "am_adam")
    assert cfg.roles["analyst"].voice == "am_adam"
    cfg = set_config_value(cfg, "roles.analyst.engine", "pykokoro")
    assert cfg.roles["analyst"].engine == "kokoro"
    with pytest.raises(KeyError, match="unknown config key"):
        set_config_value(cfg, "voices.kokoro.roles.analyst", "am_adam")


def test_reader_policy_defaults_and_validation() -> None:
    cfg = ReaderSettings()
    assert cfg.pause_mode == "auto"
    assert cfg.spacy == "auto"
    assert cfg.short_sentence == "phrase"
    with pytest.raises(ValueError):
        set_config_value(cfg, "unit", "word")
    with pytest.raises(ValueError, match="reader.short_sentence"):
        set_config_value(cfg, "short_sentence", "auto")


def test_reader_voice_level_and_finite_speed_round_trip(tmp_path: Path) -> None:
    cfg = ReaderSettings(voice_level="calibrated")
    path = tmp_path / "voice-level.toml"
    save_config(cfg, path)
    assert load_config(path).reader.voice_level == "calibrated"
    assert (
        set_config_value(ReaderSettings(), "voice_level", "calibrated").voice_level == "calibrated"
    )
    with pytest.raises(ValueError, match="reader.voice_level"):
        set_config_value(ReaderSettings(), "voice_level", "unknown")
    with pytest.raises(ValueError, match="finite"):
        set_config_value(ReaderSettings(), "speed", float("nan"))


def test_role_target_config_round_trip_uses_canonical_engine_ids(tmp_path: Path) -> None:
    target = VoiceTarget(
        engine="pipersynth",
        voice="en_US-amy-medium",
        target_id="amy-asset",
    )
    cfg = ReadioConfig(roles={"guest": target})
    path = tmp_path / "roles.toml"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.roles["guest"] == VoiceTarget("piper", "en_US-amy-medium", "amy-asset")
    assert role_targets(loaded)["guest"] == loaded.roles["guest"]
    serialized = path.read_text(encoding="utf-8")
    assert 'engine = "piper"' in serialized
    assert "selector" not in serialized


def test_v03_config_requires_explicit_migration_and_gets_backup(tmp_path: Path) -> None:
    path = tmp_path / "legacy.toml"
    path.write_text(
        "schema = 2\n"
        '[reader]\nengine = "kitten"\nvoice = "af_sarah"\nspacy = "required"\n'
        '[ssmd]\nvoice_provider = "pykokoro"\nvalidate_before_render = false\n'
        '[voices.pykokoro]\nids = ["af_sarah", "af_heart"]\n'
        '[voices.pykokoro.roles]\nnarrator = "af_heart"\n'
        '[voices.piper]\nids = ["en_US-amy-medium"]\n'
        '[voices.piper.roles]\nguest = "en_US-amy-medium"\n'
        '[languages.en]\nengine = "pykokoro"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="readio config migrate"):
        load_config(path)

    backup = migrate_config_file(path)
    assert backup == path.with_name("legacy.toml.v03.bak")
    assert backup.read_text(encoding="utf-8").startswith("schema = 2")
    cfg = load_config(path)
    assert cfg.schema == 3
    assert cfg.reader.engine == "kitten"
    assert cfg.reader.voice is None
    assert cfg.reader.spacy == "sm"
    assert cfg.ssmd.validate_before_render is False
    assert cfg.roles["narrator"] == VoiceTarget("kokoro", "af_heart")
    assert cfg.roles["guest"] == VoiceTarget("piper", "en_US-amy-medium")
    assert cfg.languages["en"].engine == "kokoro"
    stored = path.read_text(encoding="utf-8")
    assert "voices." not in stored
    assert "voice_provider" not in stored
    assert "pykokoro" not in stored
    assert migrate_config_file(path) is None


def test_config_migration_refuses_conflicting_provider_role_defaults() -> None:
    with pytest.raises(MigrationError, match="conflicting provider bindings"):
        migrate_config_data(
            {
                "schema": 2,
                "voices": {
                    "kokoro": {"roles": {"guest": "af_sarah"}},
                    "piper": {"roles": {"guest": "en_US-amy-medium"}},
                },
            }
        )


def test_schema_three_rejects_legacy_provider_fields(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(
        'schema = 3\n[ssmd]\nvoice_provider = "kokoro"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="ssmd.voice_provider is obsolete"):
        load_config(path)


def test_schema_three_serialization_never_emits_aliases() -> None:
    cfg = ReadioConfig(reader=ReaderSettings(engine="pykokoro"))
    stored = dumps_config(cfg)
    assert 'engine = "kokoro"' in stored
    assert "pykokoro" not in stored
    assert "[voices" not in stored
