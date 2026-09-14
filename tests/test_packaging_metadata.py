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


def test_dependency_windows_match_supported_runtime_contract() -> None:
    dependencies = _project_dependencies()

    assert "pykokoro[playback]>=0.9.5,<0.10" in dependencies
    assert "ssmd>=0.8.7,<0.9" in dependencies


def test_release_ci_targets_released_pykokoro_artifact() -> None:
    workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")

    assert "pykokoro[playback]==0.9.5" in workflow
    assert "pykokoro.git@" not in workflow
    assert "ssmd.git@" not in workflow
