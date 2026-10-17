from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_DEPENDENCIES = {
    "numpy>=1.23",
    "utterplan>=0.6.3,<0.7",
    "voicerender>=0.2.0,<0.3",
    "renderplan>=0.1.2,<0.2",
    "audiocompose>=0.3.0,<0.4",
    "audioexport>=0.1.2,<0.2",
    "ssmdconvert>=0.2.0,<0.3",
    "sounddevice>=0.4.6,<1.0",
    "tomli>=2.0; python_version < '3.11'",
}
FORBIDDEN_DISTRIBUTIONS = {
    "pykokoro",
    "pipersynth",
    "pocketsynth",
    "kittensynth",
    "supertonicsynth",
    "inflectsynth",
    "soprosynth",
    "qwen3synth",
    "onnxvoice",
    "voicefolio",
    "moondream",
    "ssmd",
    "soundfile",
    "platformdirs",
    "tomli-w",
    "typing-extensions",
}


def _metadata() -> dict[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)


def _distribution_name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9_.-]+", requirement)
    assert match is not None
    return match.group().lower().replace("_", "-")


def test_core_dependencies_match_the_specialist_stack() -> None:
    project = _metadata()["project"]
    dependencies = set(project["dependencies"])
    assert dependencies == EXPECTED_DEPENDENCIES
    assert not {_distribution_name(item) for item in dependencies} & FORBIDDEN_DISTRIBUTIONS


def test_optional_dependencies_do_not_mirror_engine_extras() -> None:
    optional = _metadata()["project"]["optional-dependencies"]
    assert set(optional) == {"spacy", "documents", "dev"}
    assert optional["spacy"] == ["utterplan[spacy]>=0.6.3,<0.7"]
    assert optional["documents"] == ["ssmdconvert[pdf,docx]>=0.2.0,<0.3"]
    assert not any("voicerender[" in item for group in optional.values() for item in group)


def test_minimum_stack_ci_targets_requested_releases() -> None:
    workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
    for requirement in (
        '"ssmdconvert[pdf,docx]==0.2.0"',
        '"utterplan[spacy]==0.6.3"',
        '"voicerender==0.2.0"',
        '"audiocompose==0.3.0"',
        '"audioexport==0.1.2"',
    ):
        assert requirement in workflow
