from datetime import datetime, timezone
from pathlib import Path

from readio.config import PathSettings, ReadioConfig
from readio.paths import automatic_render_name, make_artifact_id, resolve_render_output


def test_artifact_id_format():
    value = make_artifact_id(datetime(2026, 8, 24, 11, 14, 23, tzinfo=timezone.utc), "5f8ab31c")
    assert value == "20260824T111423Z-5f8ab31c"


def test_render_output_names(tmp_path: Path):
    cfg = ReadioConfig(paths=PathSettings(output=tmp_path / "output"))
    source = tmp_path / "episode.ssmd"
    wav_name = resolve_render_output(cfg, explicit=None, input_path=source).name
    assert wav_name.startswith("episode-") and wav_name.endswith(".wav")
    mp3_name = resolve_render_output(cfg, explicit=None, input_path=source, audio_format="mp3").name
    assert mp3_name.startswith("episode-") and mp3_name.endswith(".mp3")
    arbitrary = tmp_path / "meeting-notes.md"
    assert automatic_render_name(arbitrary).startswith("meeting-notes-")
    assert automatic_render_name(arbitrary, suffix=".m4a").endswith(".m4a")
    assert resolve_render_output(cfg, explicit=None, input_path=None).name.startswith("readio-")


def test_render_output_collision_allocates_new_name(tmp_path: Path, monkeypatch):
    output = tmp_path / "output"
    output.mkdir()
    source = tmp_path / "podcast-20260824T111423Z-5f8ab31c.ssmd"
    (output / source.with_suffix(".ogg").name).touch()
    values = iter(("20260824T111423Z-aaaaaaaa", "20260824T111423Z-bbbbbbbb"))
    monkeypatch.setattr("readio.paths.make_artifact_id", lambda: next(values))
    cfg = ReadioConfig(paths=PathSettings(output=output))
    assert resolve_render_output(
        cfg, explicit=None, input_path=source, audio_format="ogg"
    ).name.endswith("-aaaaaaaa.ogg")
