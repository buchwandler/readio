from contextlib import contextmanager
from pathlib import Path
from typing import cast

import pytest

from readio.audio import AudioSink
from readio.config import ReadioConfig
from readio.document import InputDocument
from readio.reader import prepare_input_document, render_text


class _Unit:
    def __init__(self, index: int) -> None:
        self.index = index


class _Prepared:
    def __init__(self, text: str) -> None:
        self.text = text
        self.units = tuple(_Unit(index) for index, _ in enumerate(text.split("\n\n")))


class _Pipeline:
    def __init__(self) -> None:
        self.document_text = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    @contextmanager
    def prepare_units(self, text: str, *, unit: str):
        del unit
        self.document_text = text
        yield _Prepared(text)


def test_prepare_input_document_projects_markdown_to_plain_text(tmp_path: Path):
    source = InputDocument("# Title\n\nParagraph.", tmp_path / "notes.md", "markdown")

    prepared = prepare_input_document(source)

    assert prepared.format == "text"
    assert prepared.source_path == source.source_path
    assert prepared.text == "Title.\n\nParagraph."


def test_render_selection_uses_spoken_markdown_projection(monkeypatch):
    pipeline = _Pipeline()
    selected = []

    monkeypatch.setattr("readio.reader._build_pipeline", lambda document, cfg: pipeline)
    monkeypatch.setattr(
        "readio.reader.render_prepared",
        lambda prepared, sink, indices=None: selected.append(indices) or "rendered",
    )

    source = InputDocument("# Title\n\n- first\n- second", None, "markdown")
    result = render_text(
        source, ReadioConfig(), cast(AudioSink, object()), selector="last-paragraph"
    )

    assert result == "rendered"
    assert pipeline.document_text == "Title.\n\nItem: first.\n\nItem: second."
    assert selected == [(2,)]


def test_empty_markdown_projection_uses_clear_reader_error(monkeypatch):
    monkeypatch.setattr(
        "readio.reader._build_pipeline",
        lambda document, cfg: pytest.fail("empty Markdown must fail before pipeline construction"),
    )

    with pytest.raises(ValueError, match="no text to read"):
        render_text(
            InputDocument("![](image.png)", None, "markdown"),
            ReadioConfig(),
            cast(AudioSink, object()),
        )
