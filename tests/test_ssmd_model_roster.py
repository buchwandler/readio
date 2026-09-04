from __future__ import annotations

import pytest

from readio import cli
from readio.config import ReadioConfig
from readio.document import InputDocument
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
    "de",
    "de-thorsten",
    "github",
    "fp32",
    "thorsten",
    None,
    False,
    1.0,
    "tts",
    "sentence",
    resolved_model=ResolvedModel.from_info(MODEL),
)


def test_ssmd_accepts_voice_from_active_model_roster() -> None:
    result = preflight_ssmd(
        '<div voice="thorsten">Hallo.</div>', ReadioConfig(), synthesis=SYNTHESIS
    )
    assert result.ok


def test_ssmd_rejects_legacy_voice_for_active_model() -> None:
    with pytest.raises(VoiceResolutionError, match="active model"):
        preflight_ssmd('<div voice="af_sarah">Hallo.</div>', ReadioConfig(), synthesis=SYNTHESIS)


def test_cli_preparation_uses_resolved_active_model_roster(monkeypatch) -> None:
    args = cli.build_parser().parse_args(
        ["render", "--file", "episode.ssmd", "--input-format", "ssmd"]
    )
    args._resolved_synthesis = SYNTHESIS
    monkeypatch.setattr(
        cli,
        "_read_input",
        lambda _args, _cfg: InputDocument('<div voice="thorsten">Hallo.</div>', None, "ssmd"),
    )
    document, bindings = cli._prepared_input(args, ReadioConfig())
    assert document.format == "ssmd"
    assert bindings == {}


def test_cli_preparation_rejects_legacy_voice_for_active_model(monkeypatch) -> None:
    args = cli.build_parser().parse_args(
        ["render", "--file", "episode.ssmd", "--input-format", "ssmd"]
    )
    args._resolved_synthesis = SYNTHESIS
    monkeypatch.setattr(
        cli,
        "_read_input",
        lambda _args, _cfg: InputDocument('<div voice="af_sarah">Hallo.</div>', None, "ssmd"),
    )
    with pytest.raises(VoiceResolutionError, match="de-thorsten"):
        cli._prepared_input(args, ReadioConfig())
