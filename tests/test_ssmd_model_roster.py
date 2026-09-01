from __future__ import annotations

from types import SimpleNamespace

import pytest

from readio.config import ReadioConfig
from readio.errors import VoiceResolutionError
from readio.models import ModelInfo
from readio.ssmd import preflight_ssmd
from readio.synthesis import ResolvedSynthesis

MODEL = ModelInfo(
    id="de-thorsten",
    source="github",
    languages=("de",),
    voices=("thorsten",),
    default_voice="thorsten",
    qualities=("fp32",),
    g2p_backend="kokorog2p",
    lexicons=("gold", "crane"),
    frontend="kokorog2p-de-thorsten-v1",
    status="ready",
    experimental=False,
    runtime_available=True,
    redistribution_allowed=True,
)
SYNTHESIS = ResolvedSynthesis(
    "de", "de-thorsten", "github", "fp32", "thorsten", None, False, 1.0, "tts", "sentence"
)


def test_ssmd_accepts_voice_from_active_model_roster(monkeypatch) -> None:
    monkeypatch.setattr(
        "readio.models.get_model_info", lambda *args, **kwargs: (MODEL, SimpleNamespace())
    )
    result = preflight_ssmd(
        '<div voice="thorsten">Hallo.</div>', ReadioConfig(), synthesis=SYNTHESIS
    )
    assert result.ok


def test_ssmd_rejects_legacy_voice_for_active_model(monkeypatch) -> None:
    monkeypatch.setattr(
        "readio.models.get_model_info", lambda *args, **kwargs: (MODEL, SimpleNamespace())
    )
    with pytest.raises(VoiceResolutionError, match="active model"):
        preflight_ssmd('<div voice="af_sarah">Hallo.</div>', ReadioConfig(), synthesis=SYNTHESIS)
