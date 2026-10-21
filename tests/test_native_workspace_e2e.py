from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import audiocompose
import pytest
import voicerender
from ebooklib import epub

from readio.api import (
    ComposeRequest,
    PlanRequest,
    Readio,
    RenderPlanRequest,
    RunRequest,
    VoiceRenderRequest,
)


def _write_epub(path: Path) -> None:
    book = epub.EpubBook()
    book.set_identifier("readio-e2e")
    book.set_title("Readio E2E")
    book.set_language("en")
    chapters = []
    for number in range(1, 3):
        chapter = epub.EpubHtml(
            title=f"Chapter {number}",
            file_name=f"chapter-{number}.xhtml",
            lang="en",
        )
        chapter.content = (
            f"<html><body><h1>Chapter {number}</h1>"
            f"<p>A short model-free chapter, number {number}.</p></body></html>"
        )
        book.add_item(chapter)
        chapters.append(chapter)
    book.add_item(epub.EpubNav())
    book.add_item(epub.EpubNcx())
    book.toc = [
        epub.Link(chapter.file_name, chapter.title, f"chapter-{number}")
        for number, chapter in enumerate(chapters, start=1)
    ]
    book.spine = ["nav", *chapters]
    epub.write_epub(str(path), book)


def _write_profile(path: Path) -> None:
    profile = voicerender.RenderProfile(
        roles={
            "narrator": voicerender.RoleProfile(
                language="en-US",
                binding={"engine": "kokoro", "target_id": "v1.0", "voice": "af_heart"},
            )
        },
        default_role="narrator",
    )
    voicerender.save_render_profile(profile, path)


@pytest.mark.parametrize("kind", ["document", "book"])
def test_bundle_kinds_use_the_same_model_free_full_run_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["document", "book"],
) -> None:
    source = tmp_path / ("story.txt" if kind == "document" else "story.epub")
    if kind == "document":
        source.write_text("A short document for model-free orchestration.", encoding="utf-8")
    else:
        _write_epub(source)

    service = Readio()
    workspace = service.create_workspace(source)
    profile = workspace.root / "narrator.renderprofile.toml"
    _write_profile(profile)

    observed: dict[str, Any] = {}

    def render_bundle(path: Path, **kwargs: Any) -> object:
        observed["render_path"] = path
        observed["render_variant"] = kwargs["variant"]
        return SimpleNamespace(rendered=True)

    def compose_bundle(path: Path, **kwargs: Any) -> object:
        observed["compose_path"] = path
        observed["compose_variant"] = kwargs["variant"]
        observed["compose_options"] = kwargs["options"]
        return SimpleNamespace(composed=True)

    monkeypatch.setattr(voicerender, "render_bundle", render_bundle)
    monkeypatch.setattr(audiocompose, "compose_bundle", compose_bundle)

    result = service.run(
        workspace.root,
        RunRequest(
            plan=PlanRequest(language="en-US", diagnostics="none"),
            render_plan=RenderPlanRequest(profile=profile),
            render=VoiceRenderRequest(variant="placeholder"),
            compose=ComposeRequest(variant="placeholder"),
        ),
    )

    assert result.workspace == workspace
    assert result.workspace.kind == kind
    assert result.compile.output_dir.is_dir()
    assert result.render_plan.variant == result.variant
    assert result.render.rendered is True
    assert result.compose.composed is True
    assert observed["render_path"] == workspace.root
    assert observed["compose_path"] == workspace.root
    assert observed["render_variant"] == result.variant
    assert observed["compose_variant"] == result.variant
    assert observed["compose_options"].chapters == "all"
    assert (workspace.root / "manifest.json").is_file()
    assert workspace.root.name.endswith(".ssmd-bundle")
    assert not (workspace.root / ".readio").exists()
    assert not (workspace.root / "project.json").exists()
