from pathlib import Path

import pytest

from readio.errors import RenderError
from readio.formats import (
    AUDIO_FORMATS,
    SUPPORTED_AUDIO_FORMATS,
    AudioFormat,
    audio_format_from_suffix,
    ensure_audio_format_available,
    normalize_audio_output_path,
    resolve_audio_format,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("output.wav", "wav"),
        ("output.flac", "flac"),
        ("output.mp3", "mp3"),
        ("output.m4a", "m4a"),
        ("output.ogg", "ogg"),
        ("output.opus", "opus"),
    ],
)
def test_audio_format_from_suffix(name: str, expected: str):
    assert audio_format_from_suffix(Path(name)) == expected


def test_audio_format_suffix_is_case_insensitive():
    assert audio_format_from_suffix(Path("output.MP3")) == "mp3"


def test_audio_format_defaults_to_wav():
    assert resolve_audio_format(requested=None, output=None) == "wav"


def test_explicit_audio_format():
    assert resolve_audio_format(requested="mp3", output=None) == "mp3"


def test_matching_output_suffix():
    assert resolve_audio_format(requested="m4a", output=Path("episode.m4a")) == "m4a"


def test_conflicting_output_suffix_is_rejected():
    with pytest.raises(ValueError, match=r"--format mp3 conflicts with output extension \.ogg"):
        resolve_audio_format(requested="mp3", output=Path("episode.ogg"))


def test_flac_output_suffix_is_supported():
    assert resolve_audio_format(requested=None, output=Path("episode.flac")) == "flac"


@pytest.mark.parametrize("audio_format", ["wav", "flac", "mp3", "m4a", "ogg", "opus"])
def test_extensionless_output_is_normalized(audio_format: AudioFormat):
    assert (
        normalize_audio_output_path(Path("episode"), audio_format).name == f"episode.{audio_format}"
    )


def test_explicit_case_is_preserved():
    path = Path("episode.MP3")
    assert normalize_audio_output_path(path, "mp3") == path


def test_generic_format_registry_keeps_ogg_vorbis_and_opus_distinct():
    assert "m4b" not in SUPPORTED_AUDIO_FORMATS
    assert AUDIO_FORMATS["ogg"].soundfile_subtype == "VORBIS"
    assert AUDIO_FORMATS["opus"].backend == "ffmpeg"
    assert audio_format_from_suffix(Path("audio.opus")) == "opus"


def test_missing_ffmpeg_diagnostic_names_opus(monkeypatch):
    monkeypatch.setattr("readio.formats.ffmpeg_executable", lambda: None)
    with pytest.raises(RenderError, match="OPUS output requires FFmpeg"):
        ensure_audio_format_available("opus")
