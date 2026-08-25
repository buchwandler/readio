from pathlib import Path

import pytest

from readio.formats import (
    audio_format_from_suffix,
    normalize_audio_output_path,
    resolve_audio_format,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [("output.wav", "wav"), ("output.mp3", "mp3"), ("output.m4a", "m4a"), ("output.ogg", "ogg")],
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


def test_unsupported_output_suffix_is_rejected():
    with pytest.raises(ValueError, match="unsupported audio output extension '.flac'"):
        resolve_audio_format(requested=None, output=Path("episode.flac"))


@pytest.mark.parametrize("audio_format", ["wav", "mp3", "m4a", "ogg"])
def test_extensionless_output_is_normalized(audio_format: str):
    assert normalize_audio_output_path(Path("episode"), audio_format).name == f"episode.{audio_format}"


def test_explicit_case_is_preserved():
    path = Path("episode.MP3")
    assert normalize_audio_output_path(path, "mp3") == path
