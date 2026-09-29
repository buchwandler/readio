from __future__ import annotations

import re
from pathlib import Path

import pytest

from readio.ssmd import parse_ssmd_09

GUIDE_DIR = Path(__file__).parents[1] / "llm-guides" / "ssmd"
EXPECTED_GUIDES = {
    "general-narration.md",
    "podcast-solo.md",
    "podcast-interview.md",
    "podcast-roundtable.md",
    "news-briefing.md",
    "educational-explainer.md",
    "document-summary.md",
    "funny-story.md",
    "dramatic-story.md",
    "kids-story.md",
    "audio-drama.md",
    "guided-meditation.md",
    "language-learning.md",
    "debate-pro-con.md",
    "quiz-trivia.md",
}
REQUIRED_SECTIONS = {
    "Mission",
    "Output contract",
    "Audio quality contract",
    "Target runtime",
    "Content integrity",
    "Final self-check",
    "Generation procedure",
}
SHARED_SECTIONS = REQUIRED_SECTIONS - {"Mission"}
KNOWN_DEFAULT_VOICE_IDS = {
    "af_sarah",
    "am_michael",
    "af_bella",
    "am_adam",
    "bf_emma",
    "bm_george",
}


def section(text: str, heading: str) -> str:
    match = re.search(rf"(?ms)^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text)
    assert match, f"missing section: {heading}"
    return match.group(1).strip()


def guide_texts() -> list[tuple[Path, str]]:
    paths = sorted(GUIDE_DIR.glob("*.md"))
    assert {path.name for path in paths} == EXPECTED_GUIDES
    return [(path, path.read_text(encoding="utf-8")) for path in paths]


def test_guide_catalog_uses_stable_kebab_case_names():
    paths = sorted(GUIDE_DIR.glob("*.md"))
    assert len(paths) == 15
    assert all(re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\.md", path.name) for path in paths)
    assert not (GUIDE_DIR.parent.parent / "templates").exists()


def test_guides_have_required_identity_and_sections():
    for path, text in guide_texts():
        assert len(re.findall(r"^# (?!#)", text, flags=re.MULTILINE)) == 1
        h1 = re.search(r"^# (.+)$", text, flags=re.MULTILINE).group(1)
        assert "Readio" in h1 and "SSMD" in h1
        assert "ssmd_version: '0.9'" in section(text, "Target runtime"), path.name
        assert re.search(
            r"(?m)^ssmd_version: (?:'0\.9'|\"0\.9\")$", section(text, "Recommended default header")
        ), path.name
        assert "ssmd_version: '0.9'" in section(text, "Minimal pattern example"), path.name
        headings = set(re.findall(r"^## (.+)$", text, flags=re.MULTILINE))
        assert REQUIRED_SECTIONS <= headings, path.name
        assert (
            text.index("## Output contract")
            < text.index("## Audio quality contract")
            < text.index("## Target runtime")
        )
        assert (
            text.index("## Minimal pattern example")
            < text.index("## Final self-check")
            < text.index("## Generation procedure")
        )


def test_guides_are_standalone_and_support_both_output_modes():
    for path, text in guide_texts():
        lowered = text.lower()
        assert "without python" in lowered
        assert "a readio installation" in lowered
        assert "readio agent skill" in lowered
        assert "local ssmd tooling" in lowered
        assert "local model discovery" in lowered

        output = section(text, "Output contract")
        assert "downloadable files or artifacts" in output
        assert "exactly one utf-8" in output.lower()
        assert "`.ssmd.md` filename" in output
        assert "accept `.ssmd` for compatibility" in output
        assert "fallback chat mode" in output.lower()
        assert "complete raw ssmd source" in output.lower()
        assert "do not use markdown code fences" in output.lower()
        assert "do not create helper files" in output.lower()
        assert "do not claim that readio/ssmd validation" in output.lower()


def test_readmes_recommend_ssmd_md_and_preserve_legacy_template_names():
    repo_root = Path(__file__).parents[1]
    guide_readme = (repo_root / "llm-guides" / "README.md").read_text(encoding="utf-8")
    top_readme = (repo_root / "README.md").read_text(encoding="utf-8")

    assert "downloadable .ssmd.md file" in guide_readme
    assert "Create exactly one UTF-8 `.ssmd.md` file." in guide_readme
    assert "legacy `.ssmd` inputs for compatibility" in guide_readme

    audio_model = guide_readme.split("## Audio-first quality model", 1)[1]
    assert "two independent axes" in audio_model
    assert "Structural portability" in audio_model
    assert "Listening quality" in audio_model
    assert "Valid SSMD is necessary but not sufficient" in audio_model
    assert "readio plan roles" in audio_model
    assert "destination system" in audio_model
    assert "acoustically similar voices" in audio_model
    assert not KNOWN_DEFAULT_VOICE_IDS.intersection(audio_model.split())
    assert (
        "existing runtime templates managed by `readio template` may keep their `.ssmd` filenames"
        in guide_readme
    )
    assert "one downloadable `.ssmd.md` file" in top_readme
    assert "Readio also accepts `.ssmd` for compatibility" in top_readme


def test_compatibility_and_shared_sections_do_not_drift():
    texts = [text for _, text in guide_texts()]
    for text in texts:
        target = section(text, "Target runtime")
        assert ":::{" in target
        assert "::{" in target
        assert "three ASCII colon characters" in target
        assert "followed immediately by `{`" in target
        assert "invalid" in target.casefold()
        assert (
            "- rate: `very-slow`, `slow`, `moderate`, `normal`, `brisk`, `fast`, `very-fast`"
            in target
        )
        assert (
            "- pitch: `very-low`, `low`, `moderate-low`, `normal`, `moderate-high`, `high`, `very-high`"
            in target
        )
        assert "do not generate them in new documents" in target
        assert "- volume: `silent`, `x-soft`, `soft`, `medium`, `loud`, `x-loud`" in target
        assert "Readio with SSMD 0.9 and Utterplan 0.3 support" in target
        assert "SSMD >=0.9.0,<0.10" in target
        assert "Utterplan >=0.3.0,<0.4" in target
        assert "PyKokoro with Utterplan schema-v3 support" in target
    for heading in SHARED_SECTIONS:
        assert len({section(text, heading) for text in texts}) == 1, heading


def test_shared_audio_quality_contract_prevents_prosody_as_identity():
    for path, text in guide_texts():
        quality = section(text, "Audio quality contract").casefold()
        assert "cannot see" in quality, path.name
        assert "stable symbolic voice role" in quality, path.name
        assert "prosody" in quality, path.name
        assert "speaker identity" in quality, path.name


def test_final_self_check_includes_audio_only_comprehension():
    for path, text in guide_texts():
        final = section(text, "Final self-check").casefold()
        assert "rendered audio" in final, path.name
        assert "who is speaking" in final, path.name
        assert "transitions" in final, path.name


def test_generation_procedure_plans_requested_duration():
    for path, text in guide_texts():
        procedure = section(text, "Generation procedure").casefold()
        assert "spoken-word and pause budget" in procedure, path.name


def test_minimal_examples_use_only_portable_roles_and_no_pitch():
    allowed_roles = {"narrator", "host", "guest", "analyst"}
    for path, text in guide_texts():
        minimal = section(text, "Minimal pattern example")
        example = re.search(r"```ssmd\n(.*?)```", minimal, flags=re.DOTALL).group(1)
        assert 'pitch="' not in example, path.name
        roles = set(re.findall(r'\bvoice="([^"]+)"', example))
        assert roles <= allowed_roles, (path.name, roles)


def test_minimal_examples_warn_against_content_cloning():
    for path, text in guide_texts():
        minimal = section(text, "Minimal pattern example").casefold()
        assert "not a content template" in minimal, path.name
        assert "sequence of events" in minimal or "rhetorical structure" in minimal, path.name


def test_use_case_duration_models_are_format_specific():
    heuristics = {
        "audio-drama.md": "120–150 spoken words per minute",
        "debate-pro-con.md": "135–155 spoken words per minute",
        "document-summary.md": "140–160 spoken words per minute",
        "dramatic-story.md": "115–145 spoken words per minute",
        "educational-explainer.md": "125–150 spoken words per minute",
        "funny-story.md": "120–150 spoken words per minute",
        "general-narration.md": "140–160 spoken words per minute",
        "guided-meditation.md": "70–100 spoken words per minute plus explicit silence",
        "kids-story.md": "105–135 spoken words per minute",
        "language-learning.md": "plan duration by learning cycle",
        "news-briefing.md": "145–165 spoken words per minute",
        "podcast-interview.md": "135–155 spoken words per minute",
        "podcast-roundtable.md": "135–155 spoken words per minute",
        "podcast-solo.md": "140–160 spoken words per minute",
        "quiz-trivia.md": "plan duration by question cycle",
    }
    for path, text in guide_texts():
        use_case = (
            section(text, "Recommended structure")
            + "\n"
            + section(text, "Use-case writing and performance rules")
        ).casefold()
        assert heuristics[path.name] in use_case, path.name


@pytest.mark.parametrize(
    "name",
    ["funny-story.md", "dramatic-story.md", "kids-story.md", "audio-drama.md"],
)
def test_narrative_guides_require_audio_context_and_stable_roles(name):
    text = (GUIDE_DIR / name).read_text(encoding="utf-8")
    voice = section(text, "Use-case voice design").casefold()
    rules = section(text, "Use-case writing and performance rules").casefold()

    assert "stable" in voice
    assert "narrator" in voice
    assert any(term in rules for term in ("action", "narrat", "introduc", "arrival"))


def test_conversational_guides_require_turn_dependency():
    interview = section(
        (GUIDE_DIR / "podcast-interview.md").read_text(encoding="utf-8"),
        "Use-case writing and performance rules",
    ).casefold()
    roundtable_text = (GUIDE_DIR / "podcast-roundtable.md").read_text(encoding="utf-8")
    roundtable = (
        section(roundtable_text, "Recommended structure")
        + "\n"
        + section(roundtable_text, "Use-case writing and performance rules")
    ).casefold()

    assert "preceding answer" in interview
    assert "respond" in roundtable


def test_guides_keep_voice_policy_model_agnostic():
    for path, text in guide_texts():
        assert not KNOWN_DEFAULT_VOICE_IDS.intersection(text.split())
        assert "Do not invent concrete model or voice IDs" in text
        assert "voice_bindings" in text
        assert "caller explicitly supplies concrete provider/model-valid voice IDs" in text
        assert "No invented concrete voice IDs" in section(text, "Final self-check")
        assert "unexpanded placeholders" in section(text, "Final self-check")


def test_minimal_examples_parse_with_ssmd_when_available():
    ssmd = pytest.importorskip("ssmd")
    for path, text in guide_texts():
        minimal = section(text, "Minimal pattern example")
        blocks = re.findall(r"```ssmd\n(.*?)```", minimal, flags=re.DOTALL)
        assert len(blocks) == 1, path.name
        example = blocks[0]
        assert "ssmd_version: '0.9'" in example, path.name
        all_ssmd_blocks = re.findall(r"```ssmd\n(.*?)```", text, flags=re.DOTALL)
        for ssmd_source in all_ssmd_blocks:
            assert re.search(r"(?m)^::\{", ssmd_source) is None, (
                f"{path.name} contains a two-colon directive opening"
            )
            parse_ssmd_09(ssmd_source)
        assert not re.search(r"<[^>]*\.\.\.[^>]*>", example)
        assert not any(voice_id in example for voice_id in KNOWN_DEFAULT_VOICE_IDS)
        ssmd.parse_ssmd(example, strict_parse=True)


def test_evaluation_corpus_covers_every_guide_without_model_ci():
    eval_dir = Path(__file__).parents[1] / "llm-guides" / "evals"
    prompts_dir = eval_dir / "prompts"
    prompt_paths = set(prompts_dir.glob("*.md"))
    prompt_names = {path.stem for path in prompt_paths}

    expected_guides = {Path(name).stem for name in EXPECTED_GUIDES}
    extra_names = {
        "audio-drama-source",
        "dramatic-story-constraints",
        "funny-story-source",
        "kids-story-constraints",
        "podcast-interview-source",
        "podcast-roundtable-source",
    }
    assert len(prompt_paths) == 21
    assert {
        name for name in prompt_names if not name.endswith(("-source", "-constraints"))
    } == expected_guides
    assert {
        name for name in prompt_names if name.endswith(("-source", "-constraints"))
    } == extra_names

    readme = (eval_dir / "README.md").read_text(encoding="utf-8").casefold()
    rubric = (eval_dir / "rubric.md").read_text(encoding="utf-8").casefold()
    assert "normal ci must not require a model" in readme
    for dimension in (
        "valid artifact shape",
        "role stability",
        "speaker distinguishability by role",
        "audio-only comprehension",
        "scene/topic transitions",
        "requested length/duration fit",
        "prosody restraint",
        "pause usefulness",
        "example non-cloning",
        "natural spoken language",
        "ending quality",
        "source fidelity",
        "caveats and uncertainty",
        "invented quotes or persona details",
    ):
        assert dimension in rubric
