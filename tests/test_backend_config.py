from __future__ import annotations

from pathlib import Path

from readio.config import LanguageSettings, ReaderSettings, ReadioConfig, dumps_config, load_config


def test_engine_defaults_and_round_trips(tmp_path: Path) -> None:
    cfg = ReadioConfig(
        reader=ReaderSettings(engine="pykokoro"),
        languages={"de": LanguageSettings(engine="pipersynth")},
    )
    path = tmp_path / "config.toml"
    path.write_text(dumps_config(cfg), encoding="utf-8")

    loaded = load_config(path)
    assert loaded.reader.engine == "pykokoro"
    assert loaded.languages["de"].engine == "pipersynth"


def test_schema_two_config_without_engine_defaults_to_pykokoro(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[reader]\nlang = "en-us"\n', encoding="utf-8")

    cfg = load_config(path)
    assert cfg.reader.engine == "pykokoro"
    assert cfg.languages == {}
