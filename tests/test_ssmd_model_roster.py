from __future__ import annotations

import pytest

from readio.config import ReadioConfig
from readio.errors import VoiceResolutionError
from readio.models import ModelInfo
from readio.ssmd import preflight_ssmd
from readio.synthesis import ResolvedModel, ResolvedSynthesis

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
    language="de",
    model="de-thorsten",
    source="github",
    quality="fp32",
    voice="thorsten",
    lexicons=None,
    allow_experimental=False,
    speed=1.0,
    voice_level="off",
    pause_mode="tts",
    unit="sentence",
    resolved_model=ResolvedModel.from_info(MODEL),
)


def test_ssmd_accepts_voice_from_active_model_roster() -> None:
    result = preflight_ssmd(
        ':::{voice="thorsten"}\nHallo.\n:::', ReadioConfig(), synthesis=SYNTHESIS
    )
    assert result.ok


def test_ssmd_rejects_legacy_voice_for_active_model() -> None:
    with pytest.raises(VoiceResolutionError, match="active model"):
        preflight_ssmd(':::{voice="af_sarah"}\nHallo.\n:::', ReadioConfig(), synthesis=SYNTHESIS)
