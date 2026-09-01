from __future__ import annotations

from readio.config import ReadioConfig
from readio.document import InputDocument
from readio.reader import pipeline_config_for_document
from readio.synthesis import ResolvedSynthesis


def test_pipeline_config_forwards_model_and_named_lexicons() -> None:
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
        InputDocument("Hallo", None, "text"), ReadioConfig(), synthesis=synthesis
    )

    assert pipeline.model_source == "github"
    assert pipeline.model_variant == "de-thorsten"
    assert pipeline.model_quality == "fp32"
    assert pipeline.voice == "thorsten"
    assert pipeline.generation.lang == "de"
    assert pipeline.tokenizer_config.lexicons == ("crane",)


def test_pipeline_config_leaves_tokenizer_config_unset_without_lexicons() -> None:
    pipeline = pipeline_config_for_document(InputDocument("Hello", None, "text"), ReadioConfig())
    assert pipeline.tokenizer_config is None
