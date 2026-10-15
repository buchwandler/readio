from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import ssmdconvert
import utterplan
from audiobook_support import make_epub

from readio import book_pipeline


def _plan_artifacts(workspace: Path) -> dict[str, bytes]:
    return {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in sorted((workspace / "utterplan").glob("*.utterplan.toml"))
    }


def test_manual_and_readio_compilation_resume_the_same_native_workspace(tmp_path: Path) -> None:
    source = tmp_path / "manual-to-readio.epub"
    make_epub(source)
    book = ssmdconvert.convert_book(source).book
    workspace = tmp_path / "manual-to-readio.ssmdbook"
    ssmdconvert.write_book_bundle(book, workspace, format="directory")

    partial = utterplan.compile_book(workspace, chapters="5")
    assert partial.selected_chapter_ids == ("chapter-0005",)
    partial_artifacts = _plan_artifacts(workspace)
    assert set(partial_artifacts) == {"utterplan/chapter-0005.utterplan.toml"}

    opened = book_pipeline.open_book_workspace(workspace)
    assert opened.dirty is False
    assert not (workspace / ".readio").exists()
    native = book_pipeline.compile_book(opened, chapters="all")
    assert len(native.selected_chapter_ids) == 7
    full_artifacts = _plan_artifacts(workspace)
    assert (
        full_artifacts["utterplan/chapter-0005.utterplan.toml"]
        == partial_artifacts["utterplan/chapter-0005.utterplan.toml"]
    )

    utterplan.compile_book(workspace, chapters="all")
    assert _plan_artifacts(workspace) == full_artifacts

    for variant in ("narration-a", "narration-b"):
        variant_dir = workspace / "renderplan" / variant
        variant_dir.mkdir(parents=True)
        (variant_dir / "renderplan-set.toml").write_text("fixture\n", encoding="utf-8")
    status = book_pipeline.inspect_book_status(workspace)
    assert set(status["stages"]["variants"]) == {"narration-a", "narration-b"}
    assert status["dirty"] is False
    assert not (workspace / ".readio").exists()


def test_readio_created_workspace_continues_in_manual_utterplan(tmp_path: Path) -> None:
    source = tmp_path / "readio-to-manual.epub"
    make_epub(source)
    workspace = tmp_path / "readio-to-manual.ssmdbook"
    opened = book_pipeline.convert_book_source(source, workspace, chapters="5-6")
    assert opened.native.dirty is False

    book_pipeline.compile_book(opened, chapters="5")
    before = _plan_artifacts(workspace)
    manual = utterplan.compile_book(workspace, chapters="all")

    assert manual.selected_chapter_ids == ("chapter-0005", "chapter-0006")
    after = _plan_artifacts(workspace)
    assert (
        after["utterplan/chapter-0005.utterplan.toml"]
        == before["utterplan/chapter-0005.utterplan.toml"]
    )
    assert set(after) == {
        "utterplan/chapter-0005.utterplan.toml",
        "utterplan/chapter-0006.utterplan.toml",
    }
    assert not (workspace / ".readio").exists()


def test_variants_stay_independent_across_partial_then_full_builds(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "variants.ssmdbook"
    workspace.mkdir()
    native = SimpleNamespace(
        path=workspace,
        dirty=False,
        book=SimpleNamespace(chapters=tuple(SimpleNamespace(source_number=n) for n in range(1, 8))),
    )
    monkeypatch.setattr(book_pipeline, "_ssmdconvert_load_book_workspace", lambda _path: native)

    def chapter_numbers(selector: str) -> range | tuple[int, ...]:
        return range(1, 8) if selector == "all" else (int(selector),)

    def render_plan(path: Path, *, profile: Path, chapters: str, **_kwargs: object) -> object:
        variant = profile.name.split(".", maxsplit=1)[0]
        for number in chapter_numbers(chapters):
            artifact = path / "renderplan" / variant / f"chapter-{number:04d}.renderplan.toml"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(f"{variant}:{number}\n", encoding="utf-8")
        return SimpleNamespace(variant=variant)

    def synthesize(path: Path, *, variant: str, chapters: str, **_kwargs: object) -> object:
        for number in chapter_numbers(chapters):
            artifact = path / "voicerender" / variant / f"chapter-{number:04d}.wav"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(f"{variant}:audio:{number}".encode())
        return object()

    def compose(path: Path, *, variant: str, chapters: str, **_kwargs: object) -> object:
        for number in chapter_numbers(chapters):
            source = path / "voicerender" / variant / f"chapter-{number:04d}.wav"
            target = path / "audiocompose" / variant / f"chapter-{number:04d}.wav"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        return object()

    monkeypatch.setattr(book_pipeline, "_utterplan_render_book", render_plan)
    monkeypatch.setattr(book_pipeline, "_voicerender_render_book", synthesize)
    monkeypatch.setattr(book_pipeline, "_audiocompose_compose_book", compose)

    def build(variant: str, chapters: str) -> book_pipeline.BookBuildResult:
        return book_pipeline.build_book(
            book_pipeline.BookBuildRequest(
                workspace=workspace,
                profile=tmp_path / f"{variant}.renderprofile.toml",
                compile=False,
                chapters=chapters,
                export=False,
            )
        )

    partial = build("narration-a", "5")
    chapter_five_before = {
        stage: (workspace / stage / "narration-a" / "chapter-0005.wav").read_bytes()
        for stage in ("voicerender", "audiocompose")
    }
    independent = build("narration-b", "all")
    narration_b_before = {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in sorted((workspace / "voicerender" / "narration-b").glob("*.wav"))
    }
    completed = build("narration-a", "all")

    assert partial.variant == completed.variant == "narration-a"
    assert independent.variant == "narration-b"
    assert chapter_five_before == {
        stage: (workspace / stage / "narration-a" / "chapter-0005.wav").read_bytes()
        for stage in ("voicerender", "audiocompose")
    }
    assert len(list((workspace / "voicerender" / "narration-a").glob("*.wav"))) == 7
    assert len(list((workspace / "audiocompose" / "narration-a").glob("*.wav"))) == 7
    assert narration_b_before == {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in sorted((workspace / "voicerender" / "narration-b").glob("*.wav"))
    }
    assert (workspace / "voicerender" / "narration-a" / "chapter-0001.wav").read_bytes() != (
        workspace / "voicerender" / "narration-b" / "chapter-0001.wav"
    ).read_bytes()
    assert not (workspace / ".readio").exists()


def test_dirty_book_source_is_reported_without_automatic_refresh(tmp_path: Path) -> None:
    source = tmp_path / "dirty.epub"
    make_epub(source)
    workspace = tmp_path / "dirty.ssmdbook"
    book_pipeline.convert_book_source(source, workspace)
    manifest_before = (workspace / "manifest.json").read_bytes()
    chapter_path = workspace / "chapters" / "chapter-0005.ssmd.md"
    chapter_path.write_text(
        chapter_path.read_text(encoding="utf-8") + "\nEdited.\n", encoding="utf-8"
    )
    edited_bytes = chapter_path.read_bytes()

    status = book_pipeline.inspect_book_status(workspace)
    assert status["dirty"] is True
    assert json.loads((workspace / "manifest.json").read_text(encoding="utf-8"))
    assert (workspace / "manifest.json").read_bytes() == manifest_before
    assert chapter_path.read_bytes() == edited_bytes
    assert not (workspace / ".readio").exists()

    refreshed = ssmdconvert.refresh_book_workspace(workspace)
    assert refreshed.dirty is False
    assert not (workspace / ".readio").exists()
