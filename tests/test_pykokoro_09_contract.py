from __future__ import annotations

import re

import pykokoro
import pytest
from pykokoro import GenerationConfig, PipelineConfig, SSMDRenderConfig, discover_models
from pykokoro.tokenizer import TokenizerConfig

from readio.config import ReadioConfig
from readio.document import InputDocument
from readio.reader import pipeline_config_for_document
from readio.synthesis import ResolvedSynthesis


def _is_09_or_newer() -> bool:
    match = re.match(r"(\d+)\.(\d+)", str(pykokoro.__version__))
    return bool(match and (int(match.group(1)), int(match.group(2))) >= (0, 9))


def test_pykokoro_exposes_public_09_contract() -> None:
    if not _is_09_or_newer():
        pytest.skip(f"installed PyKokoro is {pykokoro.__version__}, not 0.9.x")
    assert callable(discover_models)
    assert GenerationConfig
    assert PipelineConfig
    assert SSMDRenderConfig
    assert TokenizerConfig


def test_readio_builds_public_pipeline_config_for_de_thorsten() -> None:
    synthesis = ResolvedSynthesis(
        language="de",
        model="de-thorsten",
        source="github",
        quality="fp32",
        voice="thorsten",
        lexicons=("crane",),
        allow_experimental=False,
        speed=1.0,
        pause_mode="tts",
        unit="sentence",
    )
    pipeline = pipeline_config_for_document(
        InputDocument("Hallo Welt.", None, "text"), ReadioConfig(), synthesis=synthesis
    )
    assert isinstance(pipeline, PipelineConfig)
    assert isinstance(pipeline.generation, GenerationConfig)
    assert pipeline.model_source == "github"
    assert pipeline.model_variant == "de-thorsten"
    assert pipeline.model_quality == "fp32"
    assert pipeline.voice == "thorsten"
    assert pipeline.generation.lang == "de"
    assert isinstance(pipeline.tokenizer_config, TokenizerConfig)
    assert pipeline.tokenizer_config.lexicons == ("crane",)
    assert isinstance(pipeline.ssmd, SSMDRenderConfig)
