from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from readio import book

VARIANT = "narration--3b5f647f8f57"


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "book.ssmdbook"
    root.mkdir()
    native = SimpleNamespace(path=root)
    monkeypatch.setattr(book.ssmdconvert, "load_book_workspace", lambda _path: native)
    return root


def _request(root: Path, **kwargs: object) -> book.BookBuildRequest:
    return book.BookBuildRequest(
        workspace=root,
        profile=root.parent / "narration.renderprofile.toml",
        **kwargs,
    )


def _mock_stages(
    monkeypatch: pytest.MonkeyPatch, calls: list[tuple[str, dict[str, object]]]
) -> None:
    def compile_book(_path: Path, **kwargs: object) -> object:
        calls.append(("compile", kwargs))
        return object()

    def render_book(_path: Path, **kwargs: object) -> object:
        calls.append(("render-plan", kwargs))
        return SimpleNamespace(variant=VARIANT)

    def render_speech(_path: Path, **kwargs: object) -> object:
        calls.append(("synth", kwargs))
        return object()

    def compose_book(_path: Path, **kwargs: object) -> object:
        calls.append(("compose", kwargs))
        return object()

    def export_book(_path: Path, **kwargs: object) -> object:
        calls.append(("export", kwargs))
        return object()

    monkeypatch.setattr(book.utterplan, "compile_book", compile_book)
    monkeypatch.setattr(book.utterplan, "render_book", render_book)
    monkeypatch.setattr(book.voicerender, "render_book", render_speech)
    monkeypatch.setattr(book.audiocompose, "compose_book", compose_book)
    monkeypatch.setattr(book.audioexport, "export_book", export_book)


def test_build_delegates_in_order_and_propagates_exact_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    calls: list[tuple[str, dict[str, object]]] = []
    _mock_stages(monkeypatch, calls)

    result = book.build_book(_request(root, chapters="5-17", force=True, fail_fast=True))

    assert [stage for stage, _kwargs in calls] == [
        "compile",
        "render-plan",
        "synth",
        "compose",
        "export",
    ]
    assert calls[0][1]["chapters"] == "5-17"
    assert calls[0][1]["force"] is True
    assert calls[0][1]["fail_fast"] is True
    assert calls[1][1]["variant"] is None
    assert calls[2][1]["variant"] is VARIANT
    assert calls[3][1]["variant"] is VARIANT
    assert calls[4][1]["variant"] is VARIANT
    assert result.variant is VARIANT
    assert result.compile_result is not None
    assert result.renderplan_result is not None
    assert result.voicerender_result is not None
    assert result.compose_result is not None
    assert result.export_result is not None
    assert not list(root.rglob(".readio"))
    assert not (root / "project.json").exists()


@pytest.mark.parametrize(
    ("stop_after", "expected_stages"),
    [
        ("compile", ["compile"]),
        ("render-plan", ["compile", "render-plan"]),
        ("synth", ["compile", "render-plan", "synth"]),
        ("compose", ["compile", "render-plan", "synth", "compose"]),
        ("export", ["compile", "render-plan", "synth", "compose", "export"]),
    ],
)
def test_build_stop_after_does_not_call_later_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stop_after: str,
    expected_stages: list[str],
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    calls: list[tuple[str, dict[str, object]]] = []
    _mock_stages(monkeypatch, calls)

    book.build_book(_request(root, stop_after=stop_after))

    assert [stage for stage, _kwargs in calls] == expected_stages


@pytest.mark.parametrize("failed_stage", ["compile", "render-plan", "synth", "compose", "export"])
def test_build_propagates_specialist_exception_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_stage: str,
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    calls: list[tuple[str, dict[str, object]]] = []
    _mock_stages(monkeypatch, calls)

    class SpecialistFailure(Exception):
        pass

    failure = SpecialistFailure(f"{failed_stage} failed")
    package = {
        "compile": book.utterplan,
        "render-plan": book.utterplan,
        "synth": book.voicerender,
        "compose": book.audiocompose,
        "export": book.audioexport,
    }[failed_stage]
    function = (
        "render_book"
        if failed_stage == "render-plan"
        else {
            "compile": "compile_book",
            "synth": "render_book",
            "compose": "compose_book",
            "export": "export_book",
        }.get(failed_stage, "compile_book")
    )

    def fail(*_args: object, **_kwargs: object) -> object:
        raise failure

    monkeypatch.setattr(package, function, fail)
    with pytest.raises(SpecialistFailure) as caught:
        book.build_book(_request(root))

    assert caught.value is failure


def test_build_continues_from_existing_variant_without_initializing_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    calls: list[tuple[str, dict[str, object]]] = []
    _mock_stages(monkeypatch, calls)
    before = set(root.rglob("*"))

    result = book.build_book(
        _request(
            root,
            compile=False,
            render_plan=False,
            variant=VARIANT,
            synthesize=False,
            compose=True,
            export=False,
        )
    )

    assert [stage for stage, _kwargs in calls] == ["compose"]
    assert calls[0][1]["variant"] is VARIANT
    assert result.variant is VARIANT
    assert set(root.rglob("*")) == before


def test_open_book_delegates_validation_without_writing_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "existing.ssmdbook"
    root.mkdir()
    native = SimpleNamespace(path=root)
    calls: list[Path] = []
    monkeypatch.setattr(
        book.ssmdconvert, "load_book_workspace", lambda path: calls.append(Path(path)) or native
    )

    assert book.open_book(root) is native
    assert calls == [root]
    assert list(root.iterdir()) == []


def test_convert_book_delegates_conversion_and_workspace_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.epub"
    destination = tmp_path / "converted.ssmdbook"
    source.write_bytes(b"epub")
    converted_book = object()
    native_workspace = SimpleNamespace(path=destination)
    native_progress = SimpleNamespace(current=2, total=5, message="converting")
    progress_events: list[book.BookProgressEvent] = []
    calls: list[tuple[str, object]] = []

    def convert(path: Path, *, options: object, on_progress: object) -> object:
        calls.extend(
            (
                ("convert", path),
                ("options", options),
                ("progress", on_progress),
            )
        )
        on_progress(native_progress)  # type: ignore[operator]
        return SimpleNamespace(book=converted_book)

    def write(book_value: object, output: Path, *, format: str) -> Path:
        assert book_value is converted_book
        calls.extend((("write", output), ("format", format)))
        output.mkdir()
        return output

    monkeypatch.setattr(book.ssmdconvert, "convert_book", convert)
    monkeypatch.setattr(book.ssmdconvert, "write_book_bundle", write)
    monkeypatch.setattr(book.ssmdconvert, "load_book_workspace", lambda path: native_workspace)

    result = book.convert_book(
        source,
        destination,
        chapters="5-17",
        language="en-US",
        metadata={"title": "A title"},
        sequence_fallback_mode="preserve",
        speech_mode="preserve",
        spacy="auto",
        on_progress=progress_events.append,
    )

    assert result is native_workspace
    assert calls[0] == ("convert", source)
    options = calls[1][1]
    assert options.chapters == "5-17"
    assert options.language == "en-US"
    assert options.metadata_overrides == {"title": "A title"}
    assert options.sequence_fallback_mode == "preserve"
    assert options.speech.mode == "preserve"
    assert options.speech.spacy == "auto"
    assert calls[2][0] == "progress"
    assert progress_events[0].stage == "convert"
    assert progress_events[0].native is native_progress
    assert progress_events[0].current == 2
    assert calls[3] == ("write", destination)
    assert calls[4] == ("format", "directory")
    assert list(destination.iterdir()) == []


def test_build_forwards_native_requests_and_all_stage_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    calls: list[tuple[str, dict[str, object]]] = []
    _mock_stages(monkeypatch, calls)
    profile = tmp_path / "narration.renderprofile.toml"
    planner = book.utterplan.PlannerConfig(language="it-IT")
    compile_request = book.utterplan.BookCompileRequest(
        book=root,
        chapters="compile-selection",
        output_dir=tmp_path / "compiled",
        report=tmp_path / "compile-report.toml",
        language="it-IT",
        force=True,
        fail_fast=True,
        diagnostics="all",
        planner_config=planner,
    )
    render_request = book.utterplan.BookRenderRequest(
        book=root,
        profile=profile,
        chapters="render-selection",
        variant="requested-variant",
        utterplan_dir=tmp_path / "plans",
        output_root=tmp_path / "projected",
        report=tmp_path / "render-report.toml",
        force=True,
        fail_fast=True,
    )
    compose_options = book.audiocompose.BookComposeOptions(
        sample_rate=22050,
        channels=1,
        target_lufs=-18.0,
    )
    output_policy = book.audiocompose.OutputPolicy(sample_rate=24000, channels=1)
    paths = {
        "renderplan_root": tmp_path / "renderplans",
        "voicerender_output_root": tmp_path / "voice-output",
        "cache_dir": tmp_path / "cache",
        "compose_renderplan_root": tmp_path / "compose-renderplans",
        "compose_voicerender_root": tmp_path / "compose-voice",
        "compose_output_root": tmp_path / "compose-output",
        "media_root": tmp_path / "media",
        "save_audiojobs": tmp_path / "audiojobs",
        "output": tmp_path / "book.m4b",
        "export_profile": tmp_path / "export-profile.toml",
        "cover": tmp_path / "cover.jpg",
        "ffmpeg": tmp_path / "ffmpeg",
        "ffprobe": tmp_path / "ffprobe",
    }

    result = book.build_book(
        book.BookBuildRequest(
            workspace=root,
            profile=profile,
            chapters="downstream-selection",
            compile_request=compile_request,
            render_request=render_request,
            speech="2-4",
            reset_stale_output=True,
            render_emphasis_policy="reject",
            compose_options=compose_options,
            compose_output_policy=output_policy,
            compose_emphasis_policy="strict",
            bitrate="96k",
            export_metadata={"title": "Book"},
            export_sample_rate=44100,
            export_channels=1,
            force=True,
            fail_fast=True,
            **paths,
        )
    )

    compile_kwargs = next(kwargs for name, kwargs in calls if name == "compile")
    assert compile_kwargs["chapters"] == "compile-selection"
    assert compile_kwargs["output_dir"] == compile_request.output_dir
    assert compile_kwargs["report"] == compile_request.report
    assert compile_kwargs["language"] == "it-IT"
    assert compile_kwargs["planner_config"] is planner
    assert compile_kwargs["diagnostics"] == "all"
    render_kwargs = next(kwargs for name, kwargs in calls if name == "render-plan")
    assert render_kwargs["chapters"] == "render-selection"
    assert render_kwargs["profile"] == profile
    assert render_kwargs["variant"] == "requested-variant"
    assert render_kwargs["utterplan_dir"] == render_request.utterplan_dir
    assert render_kwargs["output_root"] == render_request.output_root
    synth_kwargs = next(kwargs for name, kwargs in calls if name == "synth")
    assert synth_kwargs["variant"] == VARIANT
    assert synth_kwargs["chapters"] == "downstream-selection"
    assert synth_kwargs["speech"] == "2-4"
    assert synth_kwargs["renderplan_root"] == paths["renderplan_root"]
    assert synth_kwargs["output_root"] == paths["voicerender_output_root"]
    assert synth_kwargs["cache_dir"] == paths["cache_dir"]
    assert synth_kwargs["reset_stale_output"] is True
    assert synth_kwargs["emphasis_policy"] == "reject"
    compose_kwargs = next(kwargs for name, kwargs in calls if name == "compose")
    assert compose_kwargs["variant"] == VARIANT
    assert compose_kwargs["options"] is compose_options
    assert compose_kwargs["output_policy"] is output_policy
    assert compose_kwargs["renderplan_root"] == paths["compose_renderplan_root"]
    assert compose_kwargs["voicerender_root"] == paths["compose_voicerender_root"]
    assert compose_kwargs["output_root"] == paths["compose_output_root"]
    assert compose_kwargs["media_root"] == paths["media_root"]
    assert compose_kwargs["save_audiojobs"] == paths["save_audiojobs"]
    assert compose_kwargs["emphasis_policy"] == "strict"
    export_kwargs = next(kwargs for name, kwargs in calls if name == "export")
    assert export_kwargs["variant"] == VARIANT
    assert export_kwargs["output"] == paths["output"]
    assert export_kwargs["profile"] == paths["export_profile"]
    assert export_kwargs["metadata"] == {"title": "Book"}
    assert export_kwargs["cover"] == paths["cover"]
    assert export_kwargs["bitrate"] == "96k"
    assert export_kwargs["sample_rate"] == 44100
    assert export_kwargs["channels"] == 1
    assert export_kwargs["ffmpeg"] == paths["ffmpeg"]
    assert export_kwargs["ffprobe"] == paths["ffprobe"]
    assert result.variant == VARIANT


def test_existing_variant_in_native_render_request_is_used_when_projection_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch)
    calls: list[tuple[str, dict[str, object]]] = []
    _mock_stages(monkeypatch, calls)
    render_request = book.utterplan.BookRenderRequest(
        book=root,
        profile=tmp_path / "profile.toml",
        variant="native-variant",
    )

    result = book.build_book(
        _request(
            root,
            compile=False,
            render_plan=False,
            render_request=render_request,
            synthesize=True,
            compose=False,
            export=False,
        )
    )

    synth_kwargs = next(kwargs for name, kwargs in calls if name == "synth")
    assert synth_kwargs["variant"] == "native-variant"
    assert result.variant == "native-variant"
