from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

from readio import skill_path


def test_packaged_skill_matches_authoring_skill() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    source_root = repository_root / "skill" / "readio"
    resource_root = repository_root / "readio" / "skills" / "readio"

    source_files = sorted(path.relative_to(source_root) for path in source_root.rglob("*.md"))
    resource_files = sorted(path.relative_to(resource_root) for path in resource_root.rglob("*.md"))

    assert resource_files == source_files
    for relative_path in source_files:
        assert (resource_root / relative_path).read_bytes() == (
            source_root / relative_path
        ).read_bytes()
    assert skill_path() == resource_root / "SKILL.md"


def test_built_wheel_installs_skill_resource(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(wheelhouse),
        ],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(wheelhouse.glob("*.whl"))

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert "readio/skills/readio/SKILL.md" in names
    assert "readio/skills/readio/references/cli.md" in names

    install_root = tmp_path / "installed"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(install_root),
            str(wheel),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(install_root)
    result = subprocess.run(
        [sys.executable, "-c", "from readio import skill_path; print(skill_path())"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    installed_skill = Path(result.stdout.strip())

    assert installed_skill == install_root / "readio" / "skills" / "readio" / "SKILL.md"
    assert installed_skill.is_file()
    assert (installed_skill.parent / "references" / "cli.md").is_file()
