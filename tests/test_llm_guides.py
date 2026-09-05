from __future__ import annotations

import re
from pathlib import Path

import pytest

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
        headings = set(re.findall(r"^## (.+)$", text, flags=re.MULTILINE))
        assert REQUIRED_SECTIONS <= headings, path.name


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
        assert "`.ssmd` filename" in output
        assert "fallback chat mode" in output.lower()
        assert "complete raw ssmd source" in output.lower()
        assert "do not use markdown code fences" in output.lower()
        assert "do not create helper files" in output.lower()
        assert "do not claim that readio/ssmd validation" in output.lower()


def test_compatibility_and_shared_sections_do_not_drift():
    texts = [text for _, text in guide_texts()]
    for text in texts:
        target = section(text, "Target runtime")
        assert "Readio 0.2.x" in target
        assert "SSMD >=0.8.6,<0.9" in target
        assert "PyKokoro 0.9.x" in target
    for heading in SHARED_SECTIONS:
        assert len({section(text, heading) for text in texts}) == 1, heading


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
        assert not re.search(r"<[^>]*\.\.\.[^>]*>", example)
        assert not any(voice_id in example for voice_id in KNOWN_DEFAULT_VOICE_IDS)
        ssmd.parse_ssmd(example, strict_parse=True)
