from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from readio import book_pipeline as pipeline


def _native_workspace(root: Path) -> SimpleNamespace:
    root.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(path=root, dirty=False)


def test_open_existing_book_is_read_only_and_ignores_legacy_state(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "novel.ssmdbook"
    root.mkdir()
    (root / "manifest.json").write_text('{"kind":"ssmdbook"}', encoding="utf-8")
    legacy = root / ".readio"
    legacy.mkdir()
    state = legacy / "project.json"
    state.write_text("leave me alone", encoding="utf-8")
    before = {path.relative_to(root) for path in root.rglob("*")}
    monkeypatch.setattr(
        pipeline, "_ssmdconvert_load_book_workspace", lambda _path: _native_workspace(root)
    )

    with pytest.warns(UserWarning, match="Legacy .readio state detected"):
        workspace = pipeline.open_book_workspace(root)

    assert workspace.root == root
    assert workspace.manifest == root / "manifest.json"
    assert workspace.dirty is False
    assert state.read_text(encoding="utf-8") == "leave me alone"
    assert {path.relative_to(root) for path in root.rglob("*")} == before
    assert workspace.utterplan_dir == root / "utterplan"
    assert not workspace.utterplan_dir.exists()


def test_open_existing_book_does_not_create_readio_or_stage_directories(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "fresh.ssmdbook"
    root.mkdir()
    monkeypatch.setattr(
        pipeline, "_ssmdconvert_load_book_workspace", lambda _path: _native_workspace(root)
    )

    workspace = pipeline.open_book_workspace(root)

    assert workspace.root == root
    assert not (root / ".readio").exists()
    assert sorted(path.name for path in root.iterdir()) == []


def test_epub_conversion_uses_ssmdconvert_options_and_writes_native_bundle(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source.epub"
    source.write_bytes(b"epub fixture")
    destination = tmp_path / "converted.ssmdbook"
    converted_book = object()
    calls: list[object] = []

    def convert(path: Path, *, options: object, on_progress: object) -> SimpleNamespace:
        calls.extend((path, options, on_progress))
        return SimpleNamespace(book=converted_book)

    def write(book: object, output: Path, *, format: str) -> Path:
        assert book is converted_book
        assert format == "directory"
        output.mkdir()
        (output / "manifest.json").write_text("{}", encoding="utf-8")
        return output

    monkeypatch.setattr(pipeline, "_ssmdconvert_convert_book", convert)
    monkeypatch.setattr(pipeline, "_ssmdconvert_write_book_bundle", write)
    monkeypatch.setattr(
        pipeline,
        "_ssmdconvert_load_book_workspace",
        lambda path: _native_workspace(Path(path)),
    )

    workspace = pipeline.convert_book_source(source, destination, chapters="5-17", language="en-US")

    assert workspace.root == destination
    assert calls[0] == source.resolve()
    assert calls[1].chapters == "5-17"
    assert calls[1].language == "en-US"
    assert calls[2] is None
    assert not (destination / ".readio").exists()


def test_build_propagates_exact_variant_and_stops_after_render_plan(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "book.ssmdbook"
    root.mkdir()
    monkeypatch.setattr(
        pipeline, "_ssmdconvert_load_book_workspace", lambda _path: _native_workspace(root)
    )
    calls: list[tuple[str, object]] = []
    variant = "narration2--3b5f647f8f57"

    def compile_stage(path: Path, **kwargs: object) -> object:
        calls.append(("compile", kwargs.get("chapters")))
        return object()

    def project_stage(path: Path, **kwargs: object) -> object:
        calls.append(("render-plan", kwargs.get("chapters")))
        return SimpleNamespace(variant=variant)

    def unexpected(*_args: object, **_kwargs: object) -> None:
        pytest.fail("stop-after render-plan must not start later stages")

    monkeypatch.setattr(pipeline, "_utterplan_compile_book", compile_stage)
    monkeypatch.setattr(pipeline, "_utterplan_render_book", project_stage)
    monkeypatch.setattr(pipeline, "_voicerender_render_book", unexpected)
    monkeypatch.setattr(pipeline, "_audiocompose_compose_book", unexpected)
    monkeypatch.setattr(pipeline, "_audioexport_export_book", unexpected)

    result = pipeline.build_book(
        pipeline.BookBuildRequest(
            workspace=root,
            profile=tmp_path / "narration.renderprofile.toml",
            chapters="5-17",
            stop_after="render-plan",
        )
    )

    assert calls == [("compile", "5-17"), ("render-plan", "5-17")]
    assert result.variant == variant
    assert result.compile_result is not None
    assert result.renderplan_result is not None
    assert result.voicerender_result is None
    assert not (root / ".readio").exists()


def test_build_passes_rendered_variant_to_every_later_stage(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "book.ssmdbook"
    root.mkdir()
    monkeypatch.setattr(
        pipeline, "_ssmdconvert_load_book_workspace", lambda _path: _native_workspace(root)
    )
    variant = "narration--abc123"
    calls: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        pipeline,
        "_utterplan_compile_book",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        pipeline,
        "_utterplan_render_book",
        lambda *_args, **_kwargs: SimpleNamespace(variant=variant),
    )

    def synth(_path: Path, *, variant: str, chapters: str, **_kwargs: object) -> object:
        calls.append(("synth", variant, chapters))
        return object()

    def compose(_path: Path, *, variant: str, chapters: str, **_kwargs: object) -> object:
        calls.append(("compose", variant, chapters))
        return object()

    def export(_path: Path, *, variant: str, chapters: str, **_kwargs: object) -> object:
        calls.append(("export", variant, chapters))
        return object()

    monkeypatch.setattr(pipeline, "_voicerender_render_book", synth)
    monkeypatch.setattr(pipeline, "_audiocompose_compose_book", compose)
    monkeypatch.setattr(pipeline, "_audioexport_export_book", export)

    result = pipeline.build_book(
        pipeline.BookBuildRequest(
            workspace=root,
            profile=tmp_path / "narration.renderprofile.toml",
            chapters="5",
        )
    )

    assert calls == [("synth", variant, "5"), ("compose", variant, "5"), ("export", variant, "5")]
    assert result.variant == variant
    assert result.export_result is not None


def test_package_error_fields_survive_pipeline_boundary(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "book.ssmdbook"
    root.mkdir()
    monkeypatch.setattr(
        pipeline, "_ssmdconvert_load_book_workspace", lambda _path: _native_workspace(root)
    )

    class UpstreamFailure(Exception):
        code = "utterplan.chapter_dirty"
        details: ClassVar[dict[str, object]] = {
            "chapter_id": "chapter-0005",
            "source_number": 5,
            "reason": "edited",
        }

    def fail(*_args: object, **_kwargs: object) -> object:
        raise UpstreamFailure("chapter source changed")

    monkeypatch.setattr(pipeline, "_utterplan_compile_book", fail)
    with pytest.raises(pipeline.BookPipelineError) as caught:
        pipeline.compile_book(root, chapters="5")

    error = caught.value
    assert error.package == "utterplan"
    assert error.code == "utterplan.chapter_dirty"
    assert error.message == "chapter source changed"
    assert error.stage == "compile"
    assert error.chapter_id == "chapter-0005"
    assert error.source_number == 5
    assert error.to_dict()["details"]["reason"] == "edited"


def test_progress_is_translated_in_memory_and_not_persisted(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "book.ssmdbook"
    root.mkdir()
    monkeypatch.setattr(
        pipeline, "_ssmdconvert_load_book_workspace", lambda _path: _native_workspace(root)
    )
    events: list[pipeline.BookPipelineEvent] = []

    def compile_stage(_path: Path, *, on_progress: object, **_kwargs: object) -> object:
        assert callable(on_progress)
        on_progress(
            SimpleNamespace(
                status="complete",
                id="chapter-0005",
                source_number=5,
                completed=1,
                total=1,
            )
        )
        return object()

    monkeypatch.setattr(pipeline, "_utterplan_compile_book", compile_stage)
    pipeline.compile_book(root, chapters="5", on_progress=events.append)

    assert [event.kind for event in events] == ["stage.started", "complete", "stage.completed"]
    assert events[1].chapter_id == "chapter-0005"
    assert events[1].source_number == 5
    assert events[1].completed == 1
    assert not (root / ".readio").exists()
    assert sorted(path.name for path in root.iterdir()) == []


def test_pipeline_imports_only_package_root_public_apis() -> None:
    tree = ast.parse(Path(pipeline.__file__).read_text(encoding="utf-8"))
    imported_modules = [
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ]

    assert not any(".cli" in module or "._" in module for module in imported_modules)
    assert not any(
        module.startswith(("audiocompose.", "audioexport.", "utterplan.", "voicerender."))
        for module in imported_modules
    )


def test_native_audiobook_api_isolated_from_legacy_project_stages() -> None:
    api_path = Path(pipeline.__file__).parent / "api" / "audiobooks.py"
    tree = ast.parse(api_path.read_text(encoding="utf-8"))
    legacy_names = {"audiobook", "project", "project_model", "project_settings", "stages"}

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module_parts = set((node.module or "").split("."))
        imported_names = {alias.name.split(".")[0] for alias in node.names}
        assert not (module_parts | imported_names) & legacy_names


def test_status_reports_owner_artifacts_and_preserves_legacy_state(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "book.ssmdbook"
    root.mkdir()
    (root / "manifest.json").write_text("{}", encoding="utf-8")
    (root / "chapters").mkdir()
    legacy = root / ".readio"
    legacy.mkdir()
    sentinel = legacy / "state.json"
    sentinel.write_text("untouched", encoding="utf-8")
    native = SimpleNamespace(
        path=root,
        dirty=True,
        book=SimpleNamespace(
            chapters=(SimpleNamespace(source_number=1), SimpleNamespace(source_number=2))
        ),
    )
    monkeypatch.setattr(pipeline, "_ssmdconvert_load_book_workspace", lambda _path: native)

    (root / "utterplan").mkdir()
    (root / "utterplan" / "compile-report.toml").write_text("report", encoding="utf-8")
    (root / "utterplan" / "one.utterplan.toml").write_text("plan", encoding="utf-8")
    for stage, report in (
        ("renderplan", "renderplan-set.toml"),
        ("voicerender", "voicerender-report.toml"),
        ("audiocompose", "compose-report.toml"),
        ("audioexport", "book.m4b.audioexport.json"),
    ):
        variant_root = root / stage / "narration--exact"
        variant_root.mkdir(parents=True)
        (variant_root / report).write_text("artifact", encoding="utf-8")
    (root / "audioexport" / "narration--exact" / "book.m4b").write_bytes(b"audio")
    (root / "renderplan" / "narration--exact" / "one.renderplan.toml").write_text(
        "plan", encoding="utf-8"
    )
    (root / "voicerender" / "narration--exact" / "one.wav").write_bytes(b"audio")
    (root / "audiocompose" / "narration--exact" / "one.wav").write_bytes(b"audio")

    with pytest.warns(UserWarning, match="Legacy .readio state detected"):
        status = pipeline.inspect_book_status(root)

    assert status["dirty"] is True
    assert status["chapter_count"] == 2
    assert status["legacy_readio_state_detected"] is True
    assert status["source"]["manifest_present"] is True
    assert status["source"]["chapters_directory_present"] is True
    assert status["stages"]["compile"]["present"] is True
    assert status["stages"]["compile"]["plan_count"] == 1
    variant = status["stages"]["variants"]["narration--exact"]
    assert variant["render_plan"]["present"] is True
    assert variant["synth"]["audio_count"] == 1
    assert variant["compose"]["present"] is True
    assert variant["export"]["present"] is True
    assert sentinel.read_text(encoding="utf-8") == "untouched"


def test_status_rejects_path_like_variant(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "book.ssmdbook"
    root.mkdir()
    monkeypatch.setattr(
        pipeline, "_ssmdconvert_load_book_workspace", lambda _path: _native_workspace(root)
    )

    with pytest.raises(pipeline.BookPipelineError) as caught:
        pipeline.inspect_book_status(root, variant="../outside")

    assert caught.value.code == "request.variant_invalid"


def test_profile_creation_delegates_to_voicerender_public_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "narration.renderprofile.toml"
    profile = object()
    captured: dict[str, object] = {}

    def create(**kwargs: object) -> object:
        captured.update(kwargs)
        return profile

    def save(value: object, path: Path) -> Path:
        assert value is profile
        captured["path"] = path
        return path

    monkeypatch.setattr(pipeline, "_voicerender_create_render_profile", create)
    monkeypatch.setattr(pipeline, "_voicerender_save_render_profile", save)

    result = pipeline.create_render_profile(
        target,
        engine="kokoro",
        target_id="en-us",
        language="en-us",
        voice="af_heart",
        options={"speed": 1.1},
        offline=True,
    )

    assert result == target
    assert captured["engine"] == "kokoro"
    assert captured["target_id"] == "en-us"
    assert captured["language"] == "en-us"
    assert captured["voice"] == "af_heart"
    assert captured["options"] == {"speed": 1.1}
    assert captured["offline"] is True
    assert captured["path"] == target
