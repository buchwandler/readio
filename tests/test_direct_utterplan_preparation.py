from __future__ import annotations

from typing import Any

from readio.document import document_from_text
from readio.planning.compiler import compile_semantic_plan
from readio.planning.policy import PlanningPolicy


def _plan(body: str) -> tuple[str, dict[str, Any]]:
    source = f'---\nssmd_version: "0.9"\ntitle: "Golden title"\n---\n{body}'
    policy = PlanningPolicy(
        unit="paragraph",
        spacy_policy="off",
        document_format="ssmd",
    )
    compiled = compile_semantic_plan(
        document_from_text(source, input_format="ssmd"),
        planning=policy,
    )
    assert policy.text_preparation == "spokenform"
    return source, compiled.plan.semantic_dict()


def test_direct_utterplan_golden_normalizes_numbers_abbreviations_and_dates() -> None:
    _, plan = _plan("Ordinary prose has 42 items, Dr. Smith, and the date 2024-02-29.")

    assert plan["texts"] == {
        "spoken": "Ordinary prose has forty two items, Doctor Smith, and the date "
        "February twenty ninth twenty twenty four.",
        "structural": "Ordinary prose has 42 items, Dr. Smith, and the date 2024-02-29.",
    }
    assert [segment["text"] for segment in plan["segments"]] == [plan["texts"]["spoken"]]
    assert [token["text"] for token in plan["tokens"]] == [
        "Ordinary",
        "prose",
        "has",
        "forty",
        "two",
        "items,",
        "Doctor",
        "Smith,",
        "and",
        "the",
        "date",
        "February",
        "twenty",
        "ninth",
        "twenty",
        "twenty",
        "four.",
    ]
    assert plan["languages"] == [
        {
            "id": "lang-0",
            "language": "en-us",
            "source": "document-default",
            "spoken_start": 0,
            "spoken_end": 104,
        }
    ]


def test_direct_utterplan_preserves_substitution_and_phoneme_directives() -> None:
    _, substitution = _plan('[Mr. Smith]{sub="Mister Smith"} greeted us.')
    assert substitution["texts"]["spoken"] == "Mr. Smith greeted us."
    assert substitution["annotations"][0]["kind"] == "sub"
    assert substitution["annotations"][0]["attrs"] == {"sub": "Mister Smith", "tag": "sub"}
    assert substitution["annotations"][0]["spoken_start"] == 0
    assert substitution["annotations"][0]["spoken_end"] == 9
    assert substitution["segments"][0]["directives"] == {"substitution": {"alias": "Mister Smith"}}
    assert [segment["text"] for segment in substitution["segments"]] == [
        "Mr. Smith",
        " greeted us.",
    ]

    _, pronunciation = _plan('[tomato]{ph="təˈmeɪtoʊ" alphabet="ipa"}.')
    assert pronunciation["texts"]["spoken"] == "tomato."
    assert pronunciation["annotations"][0]["kind"] == "phoneme"
    assert pronunciation["annotations"][0]["spoken_start"] == 0
    assert pronunciation["annotations"][0]["spoken_end"] == 6
    assert pronunciation["segments"][0]["directives"] == {
        "pronunciation": {"alphabet": "ipa", "phonemes": "təˈmeɪtoʊ"}
    }


def test_direct_utterplan_maps_language_voice_and_nested_annotations() -> None:
    _, language = _plan('[Hola mundo]{lang="es"}.')
    assert [segment["language"] for segment in language["segments"]] == ["es", "en-us"]
    assert language["languages"] == [
        {
            "id": "lang-0",
            "language": "es",
            "source": "explicit-span",
            "spoken_start": 0,
            "spoken_end": 10,
        },
        {
            "id": "lang-1",
            "language": "en-us",
            "source": "document-default",
            "spoken_start": 10,
            "spoken_end": 11,
        },
    ]
    assert [token["language"] for token in language["tokens"]] == ["es", "es", "en-us"]

    _, voice = _plan('[Hello, guest!]{voice="guest"}.')
    assert voice["segments"][0]["directives"] == {"voice": {"reference": "guest"}}
    assert voice["segments"][0]["pause_after"]["seconds"] == 0.15
    assert voice["annotations"][0]["kind"] == "voice"

    _, nested = _plan('[The [nested]{emphasis="moderate"} annotation works.]{voice="guest"}')
    assert [annotation["kind"] for annotation in nested["annotations"]] == ["voice", "emphasis"]
    assert [
        (annotation["spoken_start"], annotation["spoken_end"])
        for annotation in nested["annotations"]
    ] == [(0, 28), (3, 10)]
    assert nested["segments"][1]["directives"] == {
        "emphasis": {"level": "moderate"},
        "voice": {"reference": "guest"},
    }


def test_direct_utterplan_preserves_pause_markers_and_paragraph_boundaries() -> None:
    _, pause = _plan("Wait ...500ms @chapter")
    assert pause["markers"][0]["name"] == "chapter"
    assert pause["markers"][0]["spoken_position"] == 4
    assert pause["boundaries"][0]["seconds"] == 0.5
    assert pause["segments"][0]["pause_after"]["seconds"] == 0.5

    _, paragraphs = _plan("# Chapter One\n\nFirst paragraph.\n\nSecond paragraph.")
    assert paragraphs["texts"]["spoken"] == "Chapter One\n\nFirst paragraph.\n\nSecond paragraph."
    assert paragraphs["document_metadata"]["title"] == "Golden title"
    assert [segment["paragraph"] for segment in paragraphs["segments"]] == [0, 1, 2]
    assert [segment["text"] for segment in paragraphs["segments"]] == [
        "Chapter One",
        "First paragraph.",
        "Second paragraph.",
    ]
    assert [boundary["kind"] for boundary in paragraphs["boundaries"]] == [
        "heading",
        "paragraph",
        "paragraph",
    ]


def test_direct_utterplan_maps_pronunciation_offsets_across_spokenform_edits() -> None:
    source, plan = _plan('42 [cats tomorrow]{ph="cats tomorrow" alphabet="ipa"}.')

    assert plan["texts"] == {
        "spoken": "forty two cats tomorrow.",
        "structural": "42 cats tomorrow.",
    }
    annotation = plan["annotations"][0]
    assert annotation["kind"] == "phoneme"
    assert (annotation["structural_start"], annotation["structural_end"]) == (2, 16)
    assert (annotation["spoken_start"], annotation["spoken_end"]) == (9, 23)
    assert (annotation["source_start"], annotation["source_end"]) == (
        source.index("[cats"),
        source.index("}", source.index("[cats")) + 1,
    )
    assert plan["segments"][1]["directives"] == {
        "pronunciation": {"alphabet": "ipa", "phonemes": "cats tomorrow"}
    }
    assert [
        (token["text"], token["spoken_start"], token["spoken_end"]) for token in plan["tokens"]
    ] == [
        ("forty", 0, 5),
        ("two", 6, 9),
        ("cats", 10, 14),
        ("tomorrow.", 15, 24),
    ]


def test_direct_utterplan_preserves_unicode_punctuation() -> None:
    _, plan = _plan("Unicode: «Bonjour\u202f!» — café…")

    assert plan["texts"]["spoken"] == "Unicode: «Bonjour!» — café…"
    assert [segment["text"] for segment in plan["segments"]] == ["Unicode: «Bonjour!» — café…"]
    assert [token["text"] for token in plan["tokens"]] == [
        "Unicode:",
        "«Bonjour!»",
        "—",
        "café…",
    ]
