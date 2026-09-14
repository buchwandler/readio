from __future__ import annotations

import re

import pykokoro
from pykokoro import GenerationConfig, PipelineConfig, SSMDRenderConfig, discover_models
from pykokoro.tokenizer import TokenizerConfig

from readio.config import ReadioConfig
from readio.document import InputDocument
from readio.reader import pipeline_config_for_document
from readio.synthesis import ResolvedSynthesis


def _is_supported_pykokoro_version() -> bool:
    match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", str(pykokoro.__version__))
    if not match:
        return False
    value = tuple(int(match.group(index) or 0) for index in (1, 2, 3))
    return (0, 9, 5) <= value < (0, 10, 0)


def test_pykokoro_exposes_supported_public_contract() -> None:
    assert _is_supported_pykokoro_version(), (
        f"installed PyKokoro {pykokoro.__version__} is outside "
        "Readio's supported >=0.9.5,<0.10 range"
    )
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


def test_pykokoro_exposes_resolve_pipeline_config() -> None:
    """Verify the public resolve_pipeline_config API from 01 is available."""
    from pykokoro import resolve_pipeline_config

    assert callable(resolve_pipeline_config)
