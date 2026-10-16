from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


import pytest

ROOT = Path(__file__).parents[1]


def _project_dependencies() -> list[str]:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]["dependencies"]


def _project_optional_dependencies() -> dict[str, list[str]]:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]["optional-dependencies"]


def test_dependency_windows_match_supported_runtime_contract() -> None:
    dependencies = _project_dependencies()
    optional = _project_optional_dependencies()

    assert "ssmd>=0.9.3,<0.10" in dependencies
    assert "ssmdconvert>=0.2.0,<0.3" in dependencies
    assert "utterplan>=0.6.3,<0.7" in dependencies
    assert "voicerender>=0.1.1,<0.2" in dependencies
    assert "audiocompose>=0.2.2,<0.3" in dependencies
    assert "audioexport>=0.1.1,<0.2" in dependencies
    assert optional["documents"] == ["ssmdconvert[pdf,docx]>=0.2.0,<0.3"]
    assert not any(
        item.split("[", 1)[0].split(">", 1)[0].lower()
        in {"epub2text", "ebooklib", "pypdf", "python-docx"}
        for item in dependencies
    )
    assert "markdown-it-py>=3.0,<5.0" not in dependencies
    assert "mdit-py-plugins>=0.4,<1.0" not in dependencies
    assert "ssmd>=0.8.7,<0.9" not in dependencies
    assert "utterplan>=0.2.0,<0.3" not in dependencies
    assert "audiocompose>=0.1.1,<0.2" not in dependencies
    assert "sounddevice>=0.4.6,<1.0" in dependencies
    assert not any(item.startswith("pykokoro") for item in dependencies)


def test_audiocompose_public_book_api_contract_when_available() -> None:
    import audiocompose

    if not callable(getattr(audiocompose, "compose_book", None)):
        pytest.skip("compatible AudioCompose book API release is not installed")
    assert callable(audiocompose.compose_book)


def test_numpy_is_a_direct_dependency() -> None:
    assert "numpy>=1.23" in _project_dependencies()


def test_ci_and_wheel_smoke_target_released_engine_artifacts() -> None:
    workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
    tests_job = workflow.split("  engine-probe-python314:", maxsplit=1)[0]
    minimum_job = workflow.split("  engine-compatibility:", maxsplit=1)[0].split(
        "  semantic-stack-minimum:", maxsplit=1
    )[1]
    assert '"ssmd==0.9.3"' in minimum_job
    assert '"ssmdconvert[pdf,docx]==0.2.0"' in minimum_job
    assert '"utterplan[spacy]==0.6.3"' in minimum_job
    assert '"voicerender==0.1.1"' in minimum_job
    assert '"audiocompose==0.2.2"' in minimum_job
    assert '"audioexport==0.1.1"' in minimum_job
    assert "tests/test_plan_renderability.py" in minimum_job
    assert 'READIO_TEST_UTTERPLAN_MINIMUM: "1"' in minimum_job
    assert "tests/test_book_pipeline.py" in minimum_job
    assert "tests/test_supertonicsynth_engine.py" in workflow
    assert all(
        engine not in tests_job
        for engine in (
            "pykokoro",
            "pipersynth",
            "pocketsynth",
            "kittensynth",
            "supertonicsynth",
            "inflectsynth",
        )
    )
    base_install = tests_job.index('python -m pip install -e ".[dev]"')
    engine_free_smoke = tests_job.index("Public API import smoke without synthesis engines")
    optional_engine_install = tests_job.index('python -m pip install ".[kokoro,kitten,cpu]"')
    full_suite = tests_job.index("Run test suite")
    assert base_install < engine_free_smoke < optional_engine_install < full_suite

    for requirement in (
        "pykokoro==0.10.2",
        "pipersynth==0.2.1",
        "pocketsynth[cpu]==0.2.5",
        "kittensynth[cpu]==0.1.1",
        "supertonicsynth[cpu]==0.1.2",
        "inflectsynth[cpu]==0.1.1",
    ):
        assert requirement in workflow

    assert "pykokoro[playback]" not in workflow
    assert "kitten" in workflow
    assert "onnxvoice" not in workflow
    assert "engine-compatibility" in workflow
    assert "released-engine-api" in workflow
    assert "READIO_TEST_ENGINE" in workflow
    python314_job = workflow.split("  engine-probe-python314:", maxsplit=1)[1].split(
        "  semantic-stack-minimum:", maxsplit=1
    )[0]
    assert "inflectsynth" in python314_job
    assert 'package: "inflectsynth[cpu]"' in workflow
    assert 'specifier: "==0.1.1"' in workflow
    assert 'specifier: ">=0.1.1,<0.2"' in workflow
    assert "pykokoro.git@" not in workflow
    assert "ssmd.git@" not in workflow
    assert "0.9.9" not in workflow

    assert "write_test_pdf" in workflow
    assert "write_test_docx" in workflow
    assert "document_from_file" in workflow


def test_base_ci_and_wheel_smoke_cover_dependency_ownership() -> None:
    workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
    tests_job = workflow.split("  semantic-stack-minimum:", maxsplit=1)[0]

    assert "python -m pip check" in tests_job
    assert "readio-wheel-smoke-base" in workflow
    assert "readio-wheel-smoke-documents" in workflow
    assert "readio[documents] @ file://" in workflow
    assert "readio[verification] @ file://" in workflow
    assert "Installed-wheel smoke (verification extra)" in workflow
    assert "Installed-wheel smoke (engines)" in workflow
    assert '"inflectsynth[cpu]==0.1.1"' in workflow


OWNED_DEPENDENCIES = {
    "numpy>=1.23",
    "utterplan>=0.6.3,<0.7",
    "voicerender>=0.1.1,<0.2",
    "audiocompose>=0.2.2,<0.3",
    "audioexport>=0.1.1,<0.2",
    "ssmd>=0.9.3,<0.10",
    "ssmdconvert>=0.2.0,<0.3",
    "platformdirs>=4.0",
    "soundfile>=0.12",
    "sounddevice>=0.4.6,<1.0",
    "tomli>=2.0; python_version < '3.11'",
    "typing_extensions>=4.0",
    "tomli-w>=1.0",
    "rich-argparse>=1.7,<2",
}


def test_hard_dependencies_match_owned_surface() -> None:
    assert set(_project_dependencies()) == OWNED_DEPENDENCIES


def test_current_docs_have_no_stale_integration_references() -> None:
    for relative in ("README.md", "docs/architecture.md", "docs/index.md"):
        content = (ROOT / relative).read_text(encoding="utf-8").lower()
        for stale in ("onnxvoice requires", "ttsready", "markdown-it", "mdit-py-plugins"):
            assert stale not in content, f"{relative} still references {stale!r}"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    projects = (ROOT / "docs/projects.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")
    assert "UtterPlan >=0.6.3,<0.7" in readme
    assert "AudioExport >=0.1.1,<0.2" in readme
    assert '"schema_version": 4' in readme
    assert '"path": "plan/document.utterplan.toml"' in readme
    assert "writes no Readio `project.json` or `.readio/` state" in readme
    assert "specialist packages own the artifacts" in projects
    assert "schema v4" not in architecture


def test_engine_runtime_dependency_floors_are_declared() -> None:
    dependencies = _project_dependencies()
    optional = _project_optional_dependencies()

    assert not any("onnxvoice" in item for item in dependencies)
    assert optional["kokoro"] == ["voicerender[kokoro]>=0.1.1,<0.2"]
    assert optional["piper"] == ["voicerender[piper]>=0.1.1,<0.2"]
    assert optional["pocket"] == ["voicerender[pocket]>=0.1.1,<0.2"]
    assert optional["kitten"] == ["voicerender[kitten]>=0.1.1,<0.2"]
    assert optional["supertonic"] == ["voicerender[supertonic]>=0.1.1,<0.2"]
    assert optional["inflect"] == ["voicerender[inflect]>=0.1.1,<0.2"]
    assert optional["spacy"] == ["utterplan[spacy]>=0.6.3,<0.7"]
    assert optional["documents"] == ["ssmdconvert[pdf,docx]>=0.2.0,<0.3"]
    assert "voicerender[kokoro]>=0.1.1,<0.2" in optional["all"]
    assert "voicerender[piper]>=0.1.1,<0.2" in optional["all"]
    assert "voicerender[pocket]>=0.1.1,<0.2" in optional["all"]
    assert "voicerender[kitten]>=0.1.1,<0.2" in optional["all"]
    assert "voicerender[supertonic]>=0.1.1,<0.2" in optional["all"]
    assert "voicerender[inflect]>=0.1.1,<0.2" in optional["all"]
    assert "utterplan[spacy]>=0.6.3,<0.7" in optional["all"]
    assert all(
        dependency != "utterplan[spacy]>=0.1.4,<0.2"
        for group in optional.values()
        for dependency in group
    )
    assert "audioexport" not in optional


def test_verification_extras_keep_moondream_optional_and_versioned() -> None:
    dependencies = _project_dependencies()
    optional = _project_optional_dependencies()
    requirement = "moondream>=2.4,<3"
    assert optional["verification"] == [requirement]
    assert optional["benchmark"] == [requirement]
    assert not any(item.startswith("moondream") for item in dependencies)
    assert not any(item.startswith("moondream") for item in optional["all"])
