from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from readio.audio import AudioSink, RenderSummary
from readio.config import ReadioConfig
from readio.document import InputDocument
from readio.reader import prepare_input_document, render_text
from readio.synthesis import ResolvedSynthesis


def test_prepare_input_document_projects_markdown_to_plain_text(tmp_path: Path):
    source = InputDocument("# Title\n\nParagraph.", tmp_path / "notes.md", "markdown")

    prepared = prepare_input_document(source)

    assert prepared.format == "text"
    assert prepared.source_path == source.source_path
    assert prepared.text == "Title.\n\nParagraph."


def test_render_text_uses_spoken_markdown_projection(monkeypatch):
    captured = {}
    summary = RenderSummary(sample_rate=24000, sample_count=24000, channels=1)
    synthesis = ResolvedSynthesis(
        language="en-us",
        model=None,
        source=None,
        quality=None,
        voice=None,
        lexicons=None,
        allow_experimental=False,
        speed=1.0,
        voice_level="off",
        pause_mode="auto",
        unit="paragraph",
    )

    def resolve(_config, request):
        captured["request"] = request
        return type("Resolved", (), {"plan": type("Plan", (), {"ok": True})()})()

    def render_from_plan(plan, document, sink, **kwargs):
        captured.update(plan=plan, document=document, sink=sink, kwargs=kwargs)
        return summary

    monkeypatch.setattr("readio.plan.resolve_execution_v2", resolve)
    monkeypatch.setattr("readio.reader.render_from_plan_v2", render_from_plan)
    source = InputDocument("# Title\n\n- first\n- second", None, "markdown")
    sink = cast(AudioSink, object())

    result = render_text(
        source,
        ReadioConfig(),
        sink,
        selector="last-paragraph",
        synthesis=synthesis,
    )

    assert result is summary
    assert captured["document"].text == "Title.\n\nItem: first.\n\nItem: second."
    assert captured["request"].input.document == captured["document"]
    assert captured["request"].input.selector == "last-paragraph"
    assert captured["kwargs"]["selector"] == "last-paragraph"


def test_empty_markdown_projection_fails_before_plan_resolution(monkeypatch):
    monkeypatch.setattr(
        "readio.plan.resolve_execution_v2",
        lambda *_args, **_kwargs: pytest.fail("empty Markdown must fail before plan resolution"),
    )

    with pytest.raises(ValueError, match="no text to read"):
        render_text(InputDocument("#", None, "markdown"), ReadioConfig(), cast(AudioSink, object()))
