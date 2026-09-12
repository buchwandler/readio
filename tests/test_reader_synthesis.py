from __future__ import annotations

import pytest

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


def _synthesis(**overrides):
    values = {
        "language": "en-us",
        "model": None,
        "source": None,
        "quality": None,
        "voice": "af_sarah",
        "lexicons": None,
        "allow_experimental": False,
        "speed": 1.0,
        "pause_mode": "tts",
        "unit": "sentence",
        "spacy": "auto",
        "short_sentence": "auto",
    }
    values.update(overrides)
    return ResolvedSynthesis(**values)


@pytest.mark.parametrize(
    ("policy", "expected_use_spacy", "expected_size"),
    [
        ("auto", None, None),
        ("off", False, None),
        ("sm", True, "sm"),
        ("md", True, "md"),
        ("lg", True, "lg"),
        ("trf", True, "trf"),
    ],
)
def test_pipeline_config_maps_spacy_policies(policy, expected_use_spacy, expected_size) -> None:
    pipeline = pipeline_config_for_document(
        InputDocument("Hello", None, "text"),
        ReadioConfig(),
        synthesis=_synthesis(spacy=policy),
    )
    if policy == "auto":
        assert pipeline.tokenizer_config is None
        return
    assert pipeline.tokenizer_config is not None
    assert pipeline.tokenizer_config.use_spacy is expected_use_spacy
    assert pipeline.tokenizer_config.spacy_model_size == expected_size


@pytest.mark.parametrize(
    ("policy", "expected_mode"),
    [
        ("auto", None),
        ("off", None),
        ("wrap", "wrap"),
        ("phrase", "phrase"),
        ("randomized-phrase", "randomized-phrase"),
    ],
)
def test_pipeline_config_maps_short_sentence_policies(policy, expected_mode) -> None:
    pipeline = pipeline_config_for_document(
        InputDocument("Hello", None, "text"),
        ReadioConfig(),
        synthesis=_synthesis(short_sentence=policy),
    )
    config = pipeline.short_sentence_config
    if policy == "auto":
        assert config is None
    elif policy == "off":
        assert config is not None
        assert config.enabled is False
    else:
        assert config is not None
        assert config.enabled is True
        assert config.resolve_mode == expected_mode
