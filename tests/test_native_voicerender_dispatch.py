from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import voicerender

from readio.api.types import VoiceRenderRequest
from readio.pipeline import render
from readio.workspace import WorkspaceRef


def _workspace(tmp_path: Path, kind: str) -> WorkspaceRef:
    suffix = ".ssmdoc" if kind == "document" else ".ssmdbook"
    return WorkspaceRef(root=tmp_path / f"source{suffix}", kind=kind)  # type: ignore[arg-type]


def test_document_render_forwards_variant_roots_options_and_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, "document")
    rendered = object()
    event = SimpleNamespace(completed=2, total=5, message="segment rendered")
    captured: dict[str, Any] = {}
    normalized: list[object] = []

    def render_document(path: Path, **kwargs: Any) -> object:
        captured.update(path=path, **kwargs)
        kwargs["on_event"](event)
        return rendered

    monkeypatch.setattr(voicerender, "render_document", render_document)

    actual = render(
        workspace,
        VoiceRenderRequest(
            variant="narration--actual-hash",
            speech="selected",
            renderplan_root=tmp_path / "renderplan-custom",
            output_root=tmp_path / "rendered-custom",
            cache_dir=tmp_path / "cache-custom",
            force=True,
            reset_stale_output=True,
            fail_fast=True,
            emphasis_policy="reject",
        ),
        on_event=normalized.append,
    )

    assert actual is rendered
    assert captured["path"] == workspace.root
    assert captured["variant"] == "narration--actual-hash"
    assert captured["speech"] == "selected"
    assert captured["renderplan_root"] == tmp_path / "renderplan-custom"
    assert captured["output_root"] == tmp_path / "rendered-custom"
    assert captured["cache_dir"] == tmp_path / "cache-custom"
    assert captured["force"] is True
    assert captured["reset_stale_output"] is True
    assert captured["emphasis_policy"] == "reject"
    assert "fail_fast" not in captured
    assert normalized[0].stage == "render"
    assert normalized[0].current == 2
    assert normalized[0].total == 5
    assert normalized[0].source is event


def test_book_render_forwards_chapters_fail_fast_and_exact_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, "book")
    rendered = object()
    captured: dict[str, Any] = {}
    event = object()

    def render_book(path: Path, **kwargs: Any) -> object:
        captured.update(path=path, **kwargs)
        kwargs["on_event"](event)
        return rendered

    monkeypatch.setattr(voicerender, "render_book", render_book)
    events: list[object] = []

    actual = render(
        workspace,
        VoiceRenderRequest(
            variant="narration--abc123",
            chapters="5-17",
            speech="all",
            fail_fast=True,
        ),
        on_event=events.append,
    )

    assert actual is rendered
    assert captured["path"] == workspace.root
    assert captured["variant"] == "narration--abc123"
    assert captured["chapters"] == "5-17"
    assert captured["fail_fast"] is True
    assert events[0].stage == "render"
    assert events[0].source is event


def test_document_render_rejects_book_chapter_selectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        voicerender,
        "render_document",
        lambda *_args, **_kwargs: pytest.fail("document renderer should not run"),
    )

    with pytest.raises(ValueError, match=r"only valid for \.ssmdbook"):
        render(_workspace(tmp_path, "document"), VoiceRenderRequest("variant", chapters="2"))
