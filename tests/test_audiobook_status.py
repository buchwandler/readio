from __future__ import annotations

from pathlib import Path

import pytest

from audiobook_support import make_epub
from readio import api, book_pipeline


def test_native_status_reports_package_artifacts_and_leaves_legacy_state_untouched(
    tmp_path: Path,
) -> None:
    source = tmp_path / "status.epub"
    make_epub(source)
    workspace = tmp_path / "status.ssmdbook"
    opened = book_pipeline.convert_book_source(source, workspace, chapters="5-6")
    book_pipeline.compile_book(opened, chapters="5")
    variant_root = workspace / "renderplan" / "narration--exact"
    variant_root.mkdir(parents=True)
    renderplan_set = variant_root / "renderplan-set.toml"
    renderplan_set.write_text("fixture\n", encoding="utf-8")

    legacy = workspace / ".readio"
    legacy.mkdir()
    legacy_state = legacy / "project.json"
    legacy_state.write_text("legacy data\n", encoding="utf-8")
    before = legacy_state.read_bytes()

    with pytest.warns(UserWarning, match="Legacy .readio state detected"):
        status = api.Readio(api.ReadioConfig()).audiobooks.status(workspace)

    assert status["workspace"] == workspace.as_posix()
    assert status["dirty"] is False
    assert status["legacy_readio_state_detected"] is True
    assert status["stages"]["compile"]["present"] is True
    assert status["stages"]["variants"]["narration--exact"]["render_plan"]["present"] is True
    assert legacy_state.read_bytes() == before
    assert renderplan_set.read_text(encoding="utf-8") == "fixture\n"
