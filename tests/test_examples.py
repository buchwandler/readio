from pathlib import Path

import pytest

from readio.config import ReadioConfig
from readio.ssmd import parse_ssmd_09, preflight_ssmd

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "readio-prosody.ssmd"
SSMD_ASSETS = tuple(
    source_path
    for directory in (
        ROOT / "examples",
        ROOT / "readio" / "resources" / "templates",
        ROOT / "tests" / "fixtures",
    )
    for source_path in sorted(directory.glob("*.ssmd"))
)


@pytest.mark.parametrize(
    "source_path",
    SSMD_ASSETS,
    ids=lambda source_path: source_path.relative_to(ROOT).as_posix(),
)
def test_owned_ssmd_assets_parse_as_strict_09(source_path: Path):
    parse_ssmd_09(source_path.read_text(encoding="utf-8"), source_path=source_path)


def test_prosody_example_exists_and_covers_supported_controls():
    assert EXAMPLE.is_file()
    text = EXAMPLE.read_text(encoding="utf-8")

    assert 'volume="' in text
    assert 'rate="' in text
    assert 'pitch="' in text
    assert 'volume="2"' in text
    assert 'rate="slow"' in text
    assert 'pitch="low"' in text
    assert 'volume="loud" rate="fast" pitch="high"' in text
    assert ':::{voice="narrator" volume=' in text
    assert 'vrp="' not in text
    assert "++" not in text


def test_prosody_example_passes_readio_consumer_preflight():
    result = preflight_ssmd(
        EXAMPLE.read_text(encoding="utf-8"), ReadioConfig(), source_path=EXAMPLE
    )

    assert result.ok is True
