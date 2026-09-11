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
        spacy="off",
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
    assert pipeline.tokenizer_config.use_spacy is False


def test_pipeline_config_maps_required_spacy_policy() -> None:
    synthesis = ResolvedSynthesis(
        language="en-us",
        model=None,
        source=None,
        quality=None,
        voice="af_sarah",
        lexicons=None,
        allow_experimental=False,
        speed=1.0,
        pause_mode="tts",
        unit="sentence",
        spacy="required",
    )
    pipeline = pipeline_config_for_document(
        InputDocument("Hello", None, "text"), ReadioConfig(), synthesis=synthesis
    )

    assert pipeline.tokenizer_config is not None
    assert pipeline.tokenizer_config.use_spacy is True


def test_pipeline_config_leaves_tokenizer_config_unset_without_lexicons() -> None:
    pipeline = pipeline_config_for_document(InputDocument("Hello", None, "text"), ReadioConfig())
    assert pipeline.tokenizer_config is None
