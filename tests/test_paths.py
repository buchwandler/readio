from datetime import datetime, timezone
from pathlib import Path

import pytest

from readio.config import PathSettings, ReadioConfig
from readio.paths import (
    automatic_render_name,
    make_artifact_id,
    resolve_render_output,
    safe_child,
)


def test_artifact_id_format():
    value = make_artifact_id(datetime(2026, 8, 24, 11, 14, 23, tzinfo=timezone.utc), "5f8ab31c")
    assert value == "20260824T111423Z-5f8ab31c"


def test_safe_child_rejects_absolute_and_parent_traversal(tmp_path: Path):
    with pytest.raises(ValueError):
        safe_child(tmp_path, "/tmp/escape")
    with pytest.raises(ValueError):
        safe_child(tmp_path, "../escape")


def test_render_output_names(tmp_path: Path):
    cfg = ReadioConfig(
        paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", tmp_path / "output")
    )
    ingest = tmp_path / "ingest" / "podcast-20260824T111423Z-5f8ab31c.ssmd"
    assert (
        resolve_render_output(cfg, explicit=None, input_path=ingest).name
        == ingest.with_suffix(".wav").name
    )
    arbitrary = tmp_path / "meeting-notes.md"
    assert automatic_render_name(arbitrary).startswith("meeting-notes-")
    assert resolve_render_output(cfg, explicit=None, input_path=None).name.startswith("readio-")


def test_render_output_collision_allocates_new_name(tmp_path: Path, monkeypatch):
    output = tmp_path / "output"
    output.mkdir()
    source = tmp_path / "podcast-20260824T111423Z-5f8ab31c.ssmd"
    (output / source.with_suffix(".wav").name).touch()
    values = iter(("20260824T111423Z-aaaaaaaa", "20260824T111423Z-bbbbbbbb"))
    monkeypatch.setattr("readio.paths.make_artifact_id", lambda: next(values))
    cfg = ReadioConfig(paths=PathSettings(tmp_path / "templates", tmp_path / "ingest", output))
    assert resolve_render_output(cfg, explicit=None, input_path=source).name.endswith(
        "-aaaaaaaa.wav"
    )
