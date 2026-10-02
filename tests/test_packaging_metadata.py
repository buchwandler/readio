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

    assert "ssmd>=0.9.2,<0.10" in dependencies
    assert "ssmdconvert>=0.1.1,<0.2" in dependencies
    assert "markdown-it-py>=3.0,<5.0" not in dependencies
    assert "mdit-py-plugins>=0.4,<1.0" not in dependencies
    assert _project_optional_dependencies()["documents"] == [
        "ssmdconvert[pdf,docx]>=0.1.1,<0.2"
    ]
    assert not any(
        item.split("[", 1)[0].split(">", 1)[0].lower()
        in {"epub2text", "ebooklib", "pypdf", "python-docx"}
        for item in dependencies
    )
    assert "utterplan>=0.3.0,<0.4" in dependencies
    assert "audiocompose>=0.2.0,<0.3" in dependencies
    assert "ssmd>=0.8.7,<0.9" not in dependencies
    assert "utterplan>=0.2.0,<0.3" not in dependencies
    assert "audiocompose>=0.1.1,<0.2" not in dependencies
    assert "sounddevice>=0.4.6,<1.0" in dependencies
    assert not any(item.startswith("pykokoro") for item in dependencies)


def test_ci_and_wheel_smoke_target_released_engine_artifacts() -> None:
    workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
    core_job = workflow.split("  ssmdconvert-minimum:", maxsplit=1)[0]
    assert all(
        engine not in core_job
        for engine in ("pykokoro", "pipersynth", "pocketsynth", "kittensynth")
    )

    for requirement in (
        "pykokoro==0.10.2",
        "pipersynth==0.2.1",
        "pocketsynth[cpu]==0.2.1",
        "kittensynth[cpu]==0.1.0",
    ):
        assert requirement in workflow

    assert "pykokoro[playback]" not in workflow
    assert "kitten" in workflow
    assert '"onnxvoice==0.2.0"' in workflow
    assert "engine-compatibility" in workflow
    assert "released-engine-api" in workflow
    assert "READIO_TEST_ENGINE" in workflow
    assert "pykokoro.git@" not in workflow
    assert "ssmd.git@" not in workflow
    assert "0.9.9" not in workflow

    assert "write_test_pdf" in workflow
    assert "write_test_docx" in workflow
    assert "document_from_file" in workflow


def test_engine_runtime_dependency_floors_are_declared() -> None:
    dependencies = _project_dependencies()
    optional = _project_optional_dependencies()

    assert "onnxvoice>=0.2.0,<0.3" in dependencies
    assert optional["kokoro"] == ["pykokoro>=0.10.2,<0.11"]
    assert "pykokoro>=0.10.2,<0.11" in optional["all"]
    assert not any("pykokoro[playback]" in item for item in optional["all"])
    assert optional["piper"] == ["pipersynth>=0.2.1,<0.3"]
    assert optional["pocket"] == ["pocketsynth[cpu]>=0.2.1,<0.3"]
    assert optional["spacy"] == ["utterplan[spacy]>=0.3.0,<0.4"]
    assert "pipersynth>=0.2.1,<0.3" in optional["all"]
    assert "pocketsynth[cpu]>=0.2.1,<0.3" in optional["all"]
    assert optional["kitten"] == ["kittensynth[cpu]>=0.1.0,<0.2"]
    assert "kittensynth[cpu]>=0.1.0,<0.2" in optional["all"]
    assert "utterplan[spacy]>=0.3.0,<0.4" in optional["all"]
    assert all(
        dependency != "utterplan[spacy]>=0.1.4,<0.2"
        for group in optional.values()
        for dependency in group
    )
