from __future__ import annotations

from pathlib import Path

from audiobook_support import make_epub
from readio import book_pipeline


def test_native_build_prefix_compiles_selected_chapters_without_readio_state(
    tmp_path: Path,
) -> None:
    source = tmp_path / "prefix.epub"
    make_epub(source)
    workspace = tmp_path / "prefix.ssmdbook"

    created = book_pipeline.convert_book_source(source, workspace, chapters="5-6")
    result = book_pipeline.build_book(
        book_pipeline.BookBuildRequest(
            workspace=created.root,
            profile=tmp_path / "narration.renderprofile.toml",
            chapters="5",
            project_renderplans=False,
            synthesize=False,
            compose=False,
            export=False,
        )
    )

    assert result.variant == ""
    assert result.compile_result is not None
    assert result.renderplan_result is None
    assert [chapter.source_number for chapter in created.native.book.chapters] == [5, 6]
    status = book_pipeline.inspect_book_status(workspace)
    assert status["dirty"] is False
    assert status["stages"]["compile"]["present"] is True
    assert status["stages"]["compile"]["plan_count"] == 1
    assert not (workspace / ".readio").exists()
