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
    return source, compiled.plan.to_dict()


def _segments(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [segment for flow in plan["flow"] for segment in flow["segments"]]


def _text(plan: dict[str, Any]) -> str:
    return "".join(segment["text"] for segment in _segments(plan))


def test_direct_utterplan_golden_normalizes_numbers_abbreviations_and_dates() -> None:
    _, plan = _plan("Ordinary prose has 42 items, Dr. Smith, and the date 2024-02-29.")

    assert _text(plan) == (
        "Ordinary prose has forty two items, Doctor Smith, and the date "
        "February twenty ninth twenty twenty four."
    )
    replacements = [
        replacement
        for segment in _segments(plan)
        for replacement in segment.get("replacements", [])
    ]
    assert {replacement["source"] for replacement in replacements} == {
        "42",
        "Dr.",
        "2024-02-29",
    }
    assert plan["document"]["title"] == "Golden title"


def test_direct_utterplan_preserves_substitution_and_phoneme_directives() -> None:
    _, substitution = _plan('[Mr. Smith]{sub="Mister Smith"} greeted us.')
    assert _text(substitution) == "Mr. Smith greeted us."
    assert _segments(substitution)[0]["directives"] == {"substitution": {"alias": "Mister Smith"}}

    _, pronunciation = _plan('[tomato]{ph="təˈmeɪtoʊ" alphabet="ipa"}.')
    assert _text(pronunciation) == "tomato."
    assert _segments(pronunciation)[0]["directives"] == {
        "pronunciation": {"alphabet": "ipa", "phonemes": "təˈmeɪtoʊ"}
    }


def test_direct_utterplan_maps_language_voice_and_nested_annotations() -> None:
    _, language = _plan('[Hola mundo]{lang="es"} Fin.')
    assert [segment["language"] for segment in _segments(language)] == ["es", "en-us"]

    _, voice = _plan('[Hello, guest!]{voice="guest"} ...150ms')
    assert _segments(voice)[0]["directives"] == {"voice": {"reference": "guest"}}
    assert any(
        segment.get("pause_after", {}).get("time") == "150ms" for segment in _segments(voice)
    )

    _, nested = _plan('[The [nested]{emphasis="moderate"} annotation works.]{voice="guest"}')
    directives = [segment["directives"] for segment in _segments(nested)]
    assert any(item.get("emphasis") == {"level": "moderate"} for item in directives)
    assert any(item.get("voice") == {"reference": "guest"} for item in directives)


def test_direct_utterplan_preserves_pause_markers_and_paragraph_boundaries() -> None:
    _, pause = _plan("Wait ...500ms @chapter")
    assert _segments(pause)[0]["markers"] == ["chapter"]
    assert _segments(pause)[0]["pause_after"]["time"] == "500ms"

    _, paragraphs = _plan("# Chapter One\n\nFirst paragraph.\n\nSecond paragraph.")
    assert _text(paragraphs) == "Chapter OneFirst paragraph.Second paragraph."
    assert paragraphs["document"]["title"] == "Golden title"
    segments = _segments(paragraphs)
    assert [segment.get("heading") for segment in segments] == [1, None, None]
    assert [segment.get("boundary_after") for segment in segments] == [
        ["paragraph"],
        ["paragraph"],
        None,
    ]


def test_direct_utterplan_preserves_pronunciation_and_unicode_punctuation() -> None:
    source, plan = _plan('42 [cats tomorrow]{ph="cats tomorrow" alphabet="ipa"}.')
    segments = _segments(plan)
    assert _text(plan) == "forty two cats tomorrow."
    assert segments[0]["replacements"][0]["source"] == "42"
    assert segments[1]["directives"]["pronunciation"] == {
        "alphabet": "ipa",
        "phonemes": "cats tomorrow",
    }
    assert "[cats" in source

    _, unicode_plan = _plan("Unicode: «Bonjour\u202f!» — café…")
    assert _text(unicode_plan) == "Unicode: «Bonjour!»— café…"
