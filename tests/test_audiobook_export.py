from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from readio import book_pipeline


def test_native_export_forwards_package_profile_variant_and_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "novel.ssmdbook"
    workspace.mkdir()
    profile = tmp_path / "audioexport.toml"
    output = tmp_path / "novel.m4b"
    cover = tmp_path / "cover.jpg"
    monkeypatch.setattr(
        book_pipeline,
        "_ssmdconvert_load_book_workspace",
        lambda _path: SimpleNamespace(path=workspace.resolve(), dirty=False),
    )
    captured: dict[str, object] = {}

    def export(path: Path, **kwargs: object) -> SimpleNamespace:
        captured["workspace"] = path
        captured.update(kwargs)
        return SimpleNamespace(variant=kwargs["variant"], output=output)

    monkeypatch.setattr(book_pipeline, "_audioexport_export_book", export)
    result = book_pipeline.export_book(
        workspace,
        variant="narration--exact",
        chapters="5-6",
        output=output,
        profile=profile,
        metadata={"title": "Novel", "author": "A. Writer"},
        cover=cover,
        bitrate="128k",
        force=True,
    )

    assert result.variant == "narration--exact"
    assert captured["workspace"] == workspace.resolve()
    assert captured["variant"] == "narration--exact"
    assert captured["chapters"] == "5-6"
    assert captured["profile"] == profile
    assert captured["metadata"] == {"title": "Novel", "author": "A. Writer"}
    assert captured["cover"] == cover
    assert captured["bitrate"] == "128k"
    assert captured["force"] is True
    assert not (workspace / ".readio").exists()
