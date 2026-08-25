from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from readio.wave import SoundFileSink, WaveSink, atomic_audio_path, atomic_wav_path


def test_wave_sink_appends_chunks_and_writes_pcm16(tmp_path: Path):
    path = tmp_path / "audio.wav"
    with WaveSink(path) as sink:
        sink.write(np.zeros(4, dtype=np.float32), 24000)
        sink.write(np.ones(3, dtype=np.float32), 24000)

    audio, sample_rate = sf.read(path)
    info = sf.info(path)
    assert sample_rate == 24000
    assert len(audio) == 7
    assert info.subtype == "PCM_16"
    assert info.channels == 1


@pytest.mark.parametrize(
    ("file_format", "subtype", "expected_format"),
    [("MP3", "MPEG_LAYER_III", "MP3"), ("OGG", "VORBIS", "OGG")],
)
def test_soundfile_sink_writes_compressed_formats(
    tmp_path: Path, file_format: str, subtype: str, expected_format: str
):
    suffix = ".mp3" if expected_format == "MP3" else ".ogg"
    path = tmp_path / f"audio{suffix}"
    with SoundFileSink(path, file_format=file_format, subtype=subtype) as sink:
        sink.write(np.zeros(2400, dtype=np.float32), 24000)
        sink.write(np.ones(1200, dtype=np.float32), 24000)

    info = sf.info(path)
    assert info.format == expected_format
    assert info.samplerate == 24000
    assert info.frames > 0


def test_wave_sink_rejects_format_changes(tmp_path: Path):
    with WaveSink(tmp_path / "audio.wav") as sink:
        sink.write(np.zeros(2), 24000)
        with pytest.raises(ValueError, match="same sample rate and channel count"):
            sink.write(np.zeros((2, 2)), 24000)


def test_soundfile_sink_rejects_writes_after_close(tmp_path: Path):
    sink = WaveSink(tmp_path / "audio.wav")
    sink.close()
    with pytest.raises(RuntimeError, match="closed"):
        sink.write(np.zeros(2), 24000)


def test_atomic_audio_path_preserves_suffix_and_commits(tmp_path: Path):
    for suffix in (".mp3", ".m4a", ".ogg"):
        path = tmp_path / f"audio{suffix}"
        with atomic_audio_path(path) as temporary:
            assert temporary.suffix == suffix
            temporary.write_bytes(b"encoded")
        assert path.read_bytes() == b"encoded"


def test_atomic_wav_path_replaces_only_after_success(tmp_path: Path):
    path = tmp_path / "audio.wav"
    with atomic_wav_path(path) as temporary, WaveSink(temporary) as sink:
        sink.write(np.zeros(2), 24000)
    assert path.exists()
    assert sf.info(path).frames == 2


def test_atomic_audio_path_rejects_existing_without_force(tmp_path: Path):
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"old")

    with pytest.raises(FileExistsError, match="--force"), atomic_audio_path(path):
        pass
    assert path.read_bytes() == b"old"


def test_atomic_audio_path_cleans_failed_render(tmp_path: Path):
    path = tmp_path / "audio.m4a"

    with pytest.raises(RuntimeError), atomic_audio_path(path) as temporary:
        temporary.write_bytes(b"partial")
        raise RuntimeError("render failed")

    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_atomic_audio_path_keeps_old_destination_on_failure(tmp_path: Path):
    path = tmp_path / "audio.ogg"
    path.write_bytes(b"old")

    with pytest.raises(RuntimeError), atomic_audio_path(path, force=True) as temporary:
        temporary.write_bytes(b"partial")
        raise RuntimeError("render failed")

    assert path.read_bytes() == b"old"
