from pathlib import Path

from readio.config import ReadioConfig
from readio.ssmd import preflight_ssmd

EXAMPLE = Path("examples/readio-prosody.ssmd")


def test_prosody_example_exists_and_covers_supported_controls():
    assert EXAMPLE.is_file()
    text = EXAMPLE.read_text(encoding="utf-8")

    assert 'volume="' in text
    assert 'rate="' in text
    assert 'pitch="' in text
    assert '{v="' in text
    assert '{r="' in text
    assert '{p="' in text
    assert 'volume="loud" rate="fast" pitch="high"' in text
    assert '<div voice="narrator" volume=' in text
    assert "vrp=\"" not in text
    assert "++" not in text


def test_prosody_example_passes_readio_consumer_preflight():
    result = preflight_ssmd(EXAMPLE.read_text(encoding="utf-8"), ReadioConfig(), source_path=EXAMPLE)

    assert result.ok is True
