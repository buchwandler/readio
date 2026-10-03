from __future__ import annotations

from pathlib import Path

from readio.config import LanguageSettings, ReaderSettings, ReadioConfig, dumps_config, load_config


def test_engine_aliases_normalize_and_round_trip(tmp_path: Path) -> None:
    cfg = ReadioConfig(
        reader=ReaderSettings(engine="pykokoro"),
        languages={"de": LanguageSettings(engine="pipersynth")},
    )
    path = tmp_path / "config.toml"
    path.write_text(dumps_config(cfg), encoding="utf-8")

    loaded = load_config(path)
    assert loaded.reader.engine == "kokoro"
    assert loaded.languages["de"].engine == "piper"


def test_schema_three_config_without_engine_defaults_to_kokoro(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('schema = 3\n\n[reader]\nlang = "en-us"\n', encoding="utf-8")

    cfg = load_config(path)
    assert cfg.reader.engine == "kokoro"
    assert cfg.languages == {}
