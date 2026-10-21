from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import ssmdconvert
from ebooklib import epub

from readio.workspace import CreateWorkspaceRequest, create_workspace, resolve_workspace


def _make_epub(path: Path) -> None:
    book = epub.EpubBook()
    book.set_identifier("workspace-test")
    book.set_title("Workspace Test")
    book.set_language("en")

    first = epub.EpubHtml(title="One", file_name="chapter-1.xhtml", lang="en")
    first.content = "<html><body><h1>One</h1><p>First chapter.</p></body></html>"
    second = epub.EpubHtml(title="Two", file_name="chapter-2.xhtml", lang="en")
    second.content = "<html><body><h1>Two</h1><p>Second chapter.</p></body></html>"
    book.add_item(first)
    book.add_item(second)
    book.add_item(epub.EpubNav())
    book.add_item(epub.EpubNcx())
    book.toc = (
        epub.Link("chapter-1.xhtml", "One", "one"),
        epub.Link("chapter-2.xhtml", "Two", "two"),
    )
    book.spine = ["nav", first, second]
    epub.write_epub(str(path), book)


def test_create_and_resolve_document_bundle(tmp_path: Path) -> None:
    source = tmp_path / "story.txt"
    source.write_text("A short story.", encoding="utf-8")

    workspace = create_workspace(source)

    assert workspace.kind == "document"
    assert workspace.root == (tmp_path / "story.ssmd-bundle").resolve()
    assert (workspace.root / "manifest.json").is_file()
    assert not (workspace.root / ".readio").exists()
    assert resolve_workspace(workspace.root) == workspace


def test_create_document_bundle_from_valid_ssmd_source(tmp_path: Path) -> None:
    source = tmp_path / "article.ssmd.md"
    source.write_text(
        '---\nssmd_version: "0.9"\ntitle: "Article"\nlanguage: en\n---\n\nHello.\n',
        encoding="utf-8",
    )

    workspace = create_workspace(source)

    assert workspace.root.name == "article.ssmd-bundle"
    assert workspace.kind == "document"
    assert (workspace.root / "manifest.json").is_file()


def test_create_epub_bundle_uses_manifest_kind(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    _make_epub(source)

    workspace = create_workspace(source)

    assert workspace.kind == "book"
    assert workspace.root == (tmp_path / "book.ssmd-bundle").resolve()
    assert (workspace.root / "manifest.json").is_file()
    assert not (workspace.root / ".readio").exists()


def test_conversion_forwards_output_and_options_to_ssmdconvert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "story.txt"
    source.write_text("A short story.", encoding="utf-8")
    output = tmp_path / "exact.ssmd-bundle"
    output.mkdir()
    progress = lambda _event: None
    captured: dict[str, object] = {}

    def convert_bundle(source_arg: Path, **kwargs: object) -> object:
        captured["source"] = source_arg
        captured.update(kwargs)
        return SimpleNamespace(output=output)

    monkeypatch.setattr(ssmdconvert, "convert_bundle", convert_bundle)
    monkeypatch.setattr(
        ssmdconvert, "load_bundle_workspace", lambda _root: SimpleNamespace(kind="document")
    )
    request = CreateWorkspaceRequest(
        source=source,
        output=output,
        language="en-US",
        chapters="2-4",
        force=True,
    )

    workspace = create_workspace(source, request, on_progress=progress)

    assert workspace.root == output.resolve()
    assert captured == {
        "source": source.resolve(),
        "output": output,
        "chapters": "2-4",
        "language": "en-US",
        "force": True,
        "on_progress": progress,
    }


def test_existing_bundle_is_resolved_without_reconversion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "story.txt"
    source.write_text("A short story.", encoding="utf-8")
    created = create_workspace(source)

    def unexpected_conversion(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("existing bundle must not be reconverted")

    monkeypatch.setattr(ssmdconvert, "convert_bundle", unexpected_conversion)
    assert create_workspace(created.root) == created


def test_rejects_wrong_suffix_packed_bundle_missing_path_and_non_directory(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match=r"must end in \.ssmd-bundle"):
        resolve_workspace(tmp_path / "not-a-bundle")

    with pytest.raises(
        ValueError, match="packed .ssmd-bundle.zip artifacts are not pipeline workspaces"
    ):
        resolve_workspace(tmp_path / "packed.ssmd-bundle.zip")

    with pytest.raises(FileNotFoundError):
        resolve_workspace(tmp_path / "missing.ssmd-bundle")

    bundle_file = tmp_path / "not-a-directory.ssmd-bundle"
    bundle_file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ValueError, match="requires an editable .ssmd-bundle directory"):
        resolve_workspace(bundle_file)


def test_loader_exception_is_propagated_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "malformed.ssmd-bundle"
    root.mkdir()
    failure = RuntimeError("invalid native manifest")

    def fail_loader(_path: Path) -> None:
        raise failure

    monkeypatch.setattr(ssmdconvert, "load_bundle_workspace", fail_loader)

    with pytest.raises(RuntimeError) as caught:
        resolve_workspace(root)

    assert caught.value is failure
