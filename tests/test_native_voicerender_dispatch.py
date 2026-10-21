from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest
import voicerender

from readio.api.types import VoiceRenderRequest
from readio.pipeline import render
from readio.workspace import WorkspaceRef


def _workspace(tmp_path: Path, kind: Literal["document", "book"]) -> WorkspaceRef:
    return WorkspaceRef(root=tmp_path / "source.ssmd-bundle", kind=kind)


@pytest.mark.parametrize("kind", ["document", "book"])
def test_render_forwards_bundle_options_for_both_kinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: Literal["document", "book"]
) -> None:
    workspace = _workspace(tmp_path, kind)
    rendered = object()
    event = SimpleNamespace(completed=2, total=5, message="segment rendered")
    captured: dict[str, Any] = {}
    normalized: list[object] = []

    def render_bundle(path: Path, **kwargs: Any) -> object:
        captured.update(path=path, **kwargs)
        kwargs["on_progress"](event)
        return rendered

    monkeypatch.setattr(voicerender, "render_bundle", render_bundle)
    actual = render(
        workspace,
        VoiceRenderRequest(
            variant="narration--exact-hash",
            chapters="5-17",
            speech="selected",
            renderplan_root=tmp_path / "renderplan-custom",
            output_root=tmp_path / "rendered-custom",
            cache_dir=tmp_path / "cache-custom",
            force=True,
            reset_stale_output=True,
            fail_fast=True,
            emphasis_policy="defer-to-compose",
        ),
        on_event=normalized.append,
    )

    assert actual is rendered
    assert captured["path"] == workspace.root
    assert captured["variant"] == "narration--exact-hash"
    assert captured["chapters"] == "5-17"
    assert captured["speech"] == "selected"
    assert captured["renderplan_root"] == tmp_path / "renderplan-custom"
    assert captured["output_root"] == tmp_path / "rendered-custom"
    assert captured["cache_dir"] == tmp_path / "cache-custom"
    assert captured["force"] is True
    assert captured["reset_stale"] is True
    assert captured["fail_fast"] is True
    assert captured["emphasis_policy"] == "defer-to-compose"
    assert normalized[0].stage == "render"
    assert normalized[0].current == 2
    assert normalized[0].total == 5
    assert normalized[0].source is event
