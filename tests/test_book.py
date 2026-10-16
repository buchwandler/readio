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
    native = SimpleNamespace(path=destination)
    calls: list[tuple[str, object]] = []

    def convert(path: Path, *, options: object, on_progress: object) -> object:
        calls.extend((("convert", path), ("options", options), ("progress", on_progress)))
        return SimpleNamespace(book=converted_book)

    def write(book_value: object, output: Path, *, format: str) -> Path:
        assert book_value is converted_book
        calls.extend((("write", output), ("format", format)))
        output.mkdir()
        return output

    monkeypatch.setattr(book.ssmdconvert, "convert_book", convert)
    monkeypatch.setattr(book.ssmdconvert, "write_book_bundle", write)
    monkeypatch.setattr(book.ssmdconvert, "load_book_workspace", lambda path: native)
    progress = lambda _event: None

    result = book.convert_book(
        source, destination, chapters="5-17", language="en-US", on_progress=progress
    )

    assert result is native
    assert calls[0] == ("convert", source)
    options = calls[1][1]
    assert options.chapters == "5-17"
    assert options.language == "en-US"
    assert calls[2] == ("progress", progress)
    assert calls[3] == ("write", destination)
    assert calls[4] == ("format", "directory")
    assert list(destination.iterdir()) == []
