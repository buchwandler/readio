from __future__ import annotations

from pathlib import Path

import pytest

from readio.config import (
    LanguageSettings,
    ReadioConfig,
    dumps_config,
    language_profile,
    load_config,
    set_config_value,
)
from readio.migrations import migrate_config_file


def test_schema_three_language_profile_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    cfg = ReadioConfig(
        languages={
            "de": LanguageSettings(
                model="de-thorsten",
                source="github",
                quality="fp32",
                voice="thorsten",
                lexicons=("gold", "crane"),
            )
        }
    )

    path.write_text(dumps_config(cfg), encoding="utf-8")
    loaded = load_config(path)

    assert loaded.schema == 3
    assert loaded.languages["de"].lexicons == ("gold", "crane")


def test_language_keys_normalize_and_reject_collisions(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "schema = 3\n\n[languages.DE_de]\nmodel = 'one'\n\n[languages.de_DE]\nmodel = 'two'\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate normalized"):
        load_config(path)

    path.write_text("schema = 3\n\n[languages.DE_de]\nmodel = 'one'\n", encoding="utf-8")
    assert load_config(path).languages["de-de"].model == "one"


def test_language_profile_prefers_exact_locale_then_base() -> None:
    cfg = ReadioConfig(
        languages={
            "de": LanguageSettings(model="base"),
            "de-at": LanguageSettings(model="austrian"),
        }
    )

    assert language_profile(cfg, "de-DE") == ("de", cfg.languages["de"])
    assert language_profile(cfg, "de_AT") == ("de-at", cfg.languages["de-at"])


def test_dotted_language_config_set_preserves_lexicon_order() -> None:
    cfg = set_config_value(ReadioConfig(), "languages.de.lexicons", "gold, crane")
    assert cfg.languages["de"].lexicons == ("gold", "crane")


def test_legacy_config_requires_migration_and_migrates_to_schema_three(tmp_path: Path) -> None:
    path = tmp_path / "legacy.toml"
    # A minimal legacy config is enough to exercise migration.
    path.write_text('[reader]\nvoice = "af_sarah"\nlang = "de"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="migrate"):
        load_config(path)

    backup = migrate_config_file(path)
    assert backup is not None
    loaded = load_config(path)
    assert loaded.schema == 3
    assert loaded.reader.lang == "de"
    assert "schema = 3" in path.read_text(encoding="utf-8")
