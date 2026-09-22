from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

ROOT = Path(__file__).parents[1]


def _project_dependencies() -> list[str]:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]["dependencies"]


def _project_optional_dependencies() -> dict[str, list[str]]:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]["optional-dependencies"]


def test_dependency_windows_match_supported_runtime_contract() -> None:
    dependencies = _project_dependencies()

    assert "ssmd>=0.8.7,<0.9" in dependencies
    assert "utterplan>=0.2.0,<0.3" in dependencies
    assert "pykokoro[playback]>=0.9.9,<0.10" not in dependencies


def test_release_ci_targets_released_pykokoro_artifact() -> None:
    workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")

    assert "pykokoro[playback]==0.9.11" in workflow
    assert "pykokoro.git@" not in workflow
    assert "ssmd.git@" not in workflow
    assert "0.9.9" not in workflow


def test_fixed_runtime_dependency_floors_are_declared() -> None:
    dependencies = _project_dependencies()
    optional = _project_optional_dependencies()

    assert "onnxvoice>=0.1.8,<0.2" in dependencies
    assert optional["kokoro"] == ["pykokoro[playback]>=0.9.11,<0.10"]
    assert "pykokoro[playback]>=0.9.11,<0.10" in optional["all"]
    assert optional["piper"] == ["pipersynth>=0.1.3,<0.2"]
    assert optional["spacy"] == ["utterplan[spacy]>=0.1.4,<0.2"]
    assert "pipersynth>=0.1.3,<0.2" in optional["all"]
    assert "utterplan[spacy]>=0.1.4,<0.2" in optional["all"]
