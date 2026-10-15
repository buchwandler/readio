from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from readio import api, book_pipeline


def test_compatibility_create_project_returns_native_workspace_without_readio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "book.epub"
    source.write_bytes(b"fixture")
    root = tmp_path / "book.ssmdbook"
    root.mkdir()
    workspace = SimpleNamespace(root=root)
    captured: dict[str, object] = {}

    def convert(path: Path, output: Path | None, **kwargs: object) -> object:
        captured["source"] = path
        captured["output"] = output
        captured.update(kwargs)
        return workspace

    monkeypatch.setattr(book_pipeline, "convert_book_source", convert)
    app = api.Readio(api.ReadioConfig())

    created = app.audiobooks.create_project_result(
        source, output=root, chapters="2-3", language="de-DE"
    )

    assert created is workspace
    assert captured["source"] == source
    assert captured["output"] == root
    assert captured["chapters"] == "2-3"
    assert captured["language"] == "de-DE"
    assert not (root / ".readio").exists()


def test_native_audiobook_creation_rejects_readio_project_settings(tmp_path: Path) -> None:
    app = api.Readio(api.ReadioConfig())
    with pytest.raises(api.InvalidRequestError) as caught:
        app.audiobooks.create_project(tmp_path / "book.epub", settings=object())

    assert caught.value.code == "audiobook.project_settings_unsupported"
    assert not (tmp_path / "book.ssmdbook" / ".readio").exists()
