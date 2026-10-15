from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from readio import api, book_pipeline, cli


def test_audiobook_cli_registers_native_stage_commands_and_init_alias() -> None:
    parser = cli.build_parser()

    for command in (
        ["audiobook", "convert", "source.epub"],
        ["audiobook", "init", "source.epub"],
        ["audiobook", "compile", "book.ssmdbook"],
        ["audiobook", "render-plan", "book.ssmdbook", "--profile", "voice.renderprofile.toml"],
        ["audiobook", "synth", "book.ssmdbook", "--variant", "narration--exact"],
        ["audiobook", "compose", "book.ssmdbook", "--variant", "narration--exact"],
        ["audiobook", "export", "book.ssmdbook", "--variant", "narration--exact"],
        [
            "audiobook",
            "build",
            "book.ssmdbook",
            "--profile",
            "voice.renderprofile.toml",
            "--stop-after",
            "render-plan",
        ],
        ["audiobook", "status", "book.ssmdbook"],
        [
            "audiobook",
            "profile",
            "create",
            "voice.renderprofile.toml",
            "--engine",
            "kokoro",
            "--target-id",
            "en-us",
            "--language",
            "en-us",
        ],
    ):
        assert callable(parser.parse_args(command).func)


def test_build_cli_passes_request_controls_and_prints_exact_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "book.ssmdbook"
    workspace.mkdir()
    profile = tmp_path / "voice.renderprofile.toml"
    variant = "narration2--3b5f647f8f57"
    captured: dict[str, object] = {}

    def build(
        request: book_pipeline.BookBuildRequest, *, on_progress: object
    ) -> book_pipeline.BookBuildResult:
        captured["request"] = request
        captured["progress"] = on_progress
        return book_pipeline.BookBuildResult(workspace, request.chapters, variant)

    monkeypatch.setattr(
        cli,
        "_book_pipeline_module",
        lambda: SimpleNamespace(BookBuildRequest=book_pipeline.BookBuildRequest, build_book=build),
    )
    args = cli.build_parser().parse_args(
        [
            "audiobook",
            "build",
            str(workspace),
            "--profile",
            str(profile),
            "--chapters",
            "5-17",
            "--stop-after",
            "render-plan",
            "--title",
            "Native title",
            "--author",
            "Writer",
            "--json",
        ]
    )

    assert args.func(args) == 0
    request = captured["request"]
    assert isinstance(request, book_pipeline.BookBuildRequest)
    assert request.workspace == workspace
    assert request.profile == profile
    assert request.chapters == "5-17"
    assert request.stop_after == "render-plan"
    assert request.metadata == {"title": "Native title", "author": "Writer"}
    payload = json.loads(capsys.readouterr().out)
    assert payload["variant"] == variant
    assert payload["result"]["variant"] == variant


def test_render_plan_cli_exposes_returned_variant_in_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "book.ssmdbook"
    workspace.mkdir()
    variant = "narration--owned-by-utterplan"
    captured: dict[str, object] = {}

    def render_plan(path: Path, **kwargs: object) -> SimpleNamespace:
        captured["path"] = path
        captured.update(kwargs)
        return SimpleNamespace(variant=variant, chapter_count=3)

    monkeypatch.setattr(
        cli, "_book_pipeline_module", lambda: SimpleNamespace(render_book_plan=render_plan)
    )
    args = cli.build_parser().parse_args(
        [
            "audiobook",
            "render-plan",
            str(workspace),
            "--profile",
            str(tmp_path / "voice.renderprofile.toml"),
            "--chapters",
            "5-17",
            "--json",
        ]
    )

    assert args.func(args) == 0
    assert captured["chapters"] == "5-17"
    payload = json.loads(capsys.readouterr().out)
    assert payload["variant"] == variant


@pytest.mark.parametrize(
    ("argv", "method_name", "expected"),
    [
        (
            [
                "audiobook",
                "compile",
                "book.ssmdbook",
                "--chapters",
                "5-17",
                "--force",
                "--fail-fast",
            ],
            "compile_book",
            {"chapters": "5-17", "force": True, "fail_fast": True},
        ),
        (
            [
                "audiobook",
                "synth",
                "book.ssmdbook",
                "--variant",
                "narration--exact",
                "--chapters",
                "5",
            ],
            "synthesize_book",
            {"variant": "narration--exact", "chapters": "5"},
        ),
        (
            [
                "audiobook",
                "compose",
                "book.ssmdbook",
                "--variant",
                "narration--exact",
                "--chapters",
                "5",
            ],
            "compose_book",
            {"variant": "narration--exact", "chapters": "5"},
        ),
        (
            [
                "audiobook",
                "export",
                "book.ssmdbook",
                "--variant",
                "narration--exact",
                "--chapters",
                "5",
                "--title",
                "A title",
            ],
            "export_book",
            {"variant": "narration--exact", "chapters": "5"},
        ),
    ],
)
def test_native_stage_commands_forward_workspace_selector_and_variant(
    argv: list[str],
    method_name: str,
    expected: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    def stage(workspace: Path, **kwargs: object) -> SimpleNamespace:
        captured["workspace"] = workspace
        captured.update(kwargs)
        return SimpleNamespace(variant=kwargs.get("variant"))

    monkeypatch.setattr(
        cli, "_book_pipeline_module", lambda: SimpleNamespace(**{method_name: stage})
    )
    args = cli.build_parser().parse_args([*argv, "--json"])

    assert args.func(args) == 0
    assert captured["workspace"] == Path("book.ssmdbook")
    for key, value in expected.items():
        assert captured[key] == value
    assert "on_progress" in captured
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_public_audiobook_facade_returns_native_workspace_and_never_creates_readio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "novel.epub"
    source.write_bytes(b"source")
    root = tmp_path / "novel.ssmdbook"
    root.mkdir()
    app = api.Readio(api.ReadioConfig())
    native = SimpleNamespace(path=root, dirty=False)
    workspace = book_pipeline.BookWorkspace(
        root=root,
        manifest=root / "manifest.json",
        chapters_dir=root / "chapters",
        utterplan_dir=root / "utterplan",
        renderplan_dir=root / "renderplan",
        voicerender_dir=root / "voicerender",
        audiocompose_dir=root / "audiocompose",
        audioexport_dir=root / "audioexport",
        native=native,
    )
    captured: dict[str, object] = {}

    def convert(path: Path, output: Path | None, **kwargs: object) -> book_pipeline.BookWorkspace:
        captured["source"] = path
        captured["output"] = output
        captured.update(kwargs)
        return workspace

    monkeypatch.setattr(book_pipeline, "convert_book_source", convert)
    result = app.audiobooks.create_project_result(
        source, chapters="2-3", output=root, language="en-US"
    )

    assert result is workspace
    assert captured["chapters"] == "2-3"
    assert captured["language"] == "en-US"
    assert not (root / ".readio").exists()

    with pytest.raises(api.InvalidRequestError) as error:
        app.audiobooks.create_project(source, settings=object())
    assert error.value.code == "audiobook.project_settings_unsupported"
    assert not (root / ".readio").exists()


def test_public_export_facade_forwards_exact_variant_and_audioexport_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "book.ssmdbook"
    workspace.mkdir()
    app = api.Readio(api.ReadioConfig())
    captured: dict[str, object] = {}

    def export(path: Path, **kwargs: object) -> SimpleNamespace:
        captured["path"] = path
        captured.update(kwargs)
        return SimpleNamespace(variant=kwargs["variant"])

    monkeypatch.setattr(book_pipeline, "export_book", export)
    options = api.AudiobookExportOptions(
        output=tmp_path / "book.m4b",
        title="Book title",
        author="Writer",
        cover=tmp_path / "cover.jpg",
        bitrate="128k",
        profile=tmp_path / "export.toml",
    )

    result = app.audiobooks.export(workspace, options, variant="narration--exact", chapters="5-17")

    assert result.variant == "narration--exact"
    assert captured["variant"] == "narration--exact"
    assert captured["chapters"] == "5-17"
    assert captured["metadata"] == {"title": "Book title", "author": "Writer"}
    assert captured["cover"] == options.cover
    assert captured["bitrate"] == options.bitrate
    assert captured["profile"] == options.profile
    assert captured["output"] == options.output


def test_cli_native_errors_keep_package_code_stage_and_variant_in_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "book.ssmdbook"
    workspace.mkdir()

    def fail(*_args: object, **_kwargs: object) -> object:
        raise book_pipeline.BookPipelineError(
            package="voicerender",
            code="voicerender.chapter_dirty",
            message="source changed",
            stage="synth",
            chapter_id="chapter-0005",
            source_number=5,
            variant="narration--exact",
            details={"cause": "source-digest-mismatch"},
        )

    monkeypatch.setattr(cli, "_book_pipeline_module", lambda: SimpleNamespace(compile_book=fail))
    with pytest.raises(SystemExit) as exited:
        cli.main(["audiobook", "compile", str(workspace), "--json"])

    assert exited.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["package"] == "voicerender"
    assert payload["code"] == "voicerender.chapter_dirty"
    assert payload["stage"] == "synth"
    assert payload["chapter_id"] == "chapter-0005"
    assert payload["source_number"] == 5
    assert payload["variant"] == "narration--exact"
    assert payload["details"]["cause"] == "source-digest-mismatch"
