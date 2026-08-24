from pathlib import Path

import pytest

from readio.config import ReaderConfig, dumps_config, load_config, set_config_value


def test_config_round_trip(tmp_path: Path):
    path = tmp_path / "config.toml"
    cfg = ReaderConfig(voice="bf_emma", lang="en-gb", speed=1.25, unit="paragraph", device="USB")
    path.write_text(dumps_config(cfg), encoding="utf-8")
    assert load_config(path) == cfg


def test_set_config_coerces_values():
    cfg = ReaderConfig()
    assert set_config_value(cfg, "speed", "1.4").speed == 1.4
    assert set_config_value(cfg, "queue_size", "4").queue_size == 4


def test_invalid_unit_rejected():
    with pytest.raises(ValueError):
        set_config_value(ReaderConfig(), "unit", "word")
