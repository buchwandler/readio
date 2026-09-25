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

    assert "ssmd>=0.9.0,<0.10" in dependencies
    assert "utterplan>=0.3.0,<0.4" in dependencies
    assert "audiocompose>=0.2.0,<0.3" in dependencies
    assert "ssmd>=0.8.7,<0.9" not in dependencies
    assert "utterplan>=0.2.0,<0.3" not in dependencies
    assert "audiocompose>=0.1.1,<0.2" not in dependencies
    assert "pykokoro[playback]>=0.9.9,<0.10" not in dependencies


def test_ci_and_wheel_smoke_target_released_engine_artifacts() -> None:
    workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")

    for requirement in (
        "pykokoro[playback]==0.10.0",
        "pipersynth==0.2.0",
        "pocketsynth[cpu]==0.2.0",
    ):
        assert requirement in workflow

    assert "pykokoro[playback]>=0.10.0,<0.11" in workflow
    assert "package: pipersynth" in workflow
    assert 'package: "pocketsynth[cpu]"' in workflow
    assert 'specifier: ">=0.2.0,<0.3"' in workflow
    assert "engine-compatibility" in workflow
    assert "READIO_TEST_ENGINE" in workflow
    assert "pykokoro.git@" not in workflow
    assert "ssmd.git@" not in workflow
    assert "0.9.9" not in workflow


def test_engine_runtime_dependency_floors_are_declared() -> None:
    dependencies = _project_dependencies()
    optional = _project_optional_dependencies()

    assert "onnxvoice>=0.1.12,<0.2" in dependencies
    assert optional["kokoro"] == ["pykokoro[playback]>=0.10.0,<0.11"]
    assert "pykokoro[playback]>=0.10.0,<0.11" in optional["all"]
    assert optional["piper"] == ["pipersynth>=0.2.0,<0.3"]
    assert optional["pocket"] == ["pocketsynth[cpu]>=0.2.0,<0.3"]
    assert optional["spacy"] == ["utterplan[spacy]>=0.3.0,<0.4"]
    assert "pipersynth>=0.2.0,<0.3" in optional["all"]
    assert "pocketsynth[cpu]>=0.2.0,<0.3" in optional["all"]
    assert "utterplan[spacy]>=0.3.0,<0.4" in optional["all"]
    assert all(
        dependency != "utterplan[spacy]>=0.1.4,<0.2"
        for group in optional.values()
        for dependency in group
    )
