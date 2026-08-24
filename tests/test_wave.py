from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from readio.wave import WaveSink, atomic_wav_path


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


def test_wave_sink_rejects_format_changes(tmp_path: Path):
    with WaveSink(tmp_path / "audio.wav") as sink:
        sink.write(np.zeros(2), 24000)
        with pytest.raises(ValueError, match="same sample rate and channel count"):
            sink.write(np.zeros((2, 2)), 24000)


def test_atomic_wav_path_replaces_only_after_success(tmp_path: Path):
    path = tmp_path / "audio.wav"
    with atomic_wav_path(path) as temporary, WaveSink(temporary) as sink:
        sink.write(np.zeros(2), 24000)
    assert path.exists()
    assert sf.info(path).frames == 2


def test_atomic_wav_path_rejects_existing_without_force(tmp_path: Path):
    path = tmp_path / "audio.wav"
    path.write_bytes(b"old")

    with pytest.raises(FileExistsError, match="--force"), atomic_wav_path(path):
        pass
    assert path.read_bytes() == b"old"


def test_atomic_wav_path_cleans_failed_render(tmp_path: Path):
    path = tmp_path / "audio.wav"

    with pytest.raises(RuntimeError), atomic_wav_path(path) as temporary:
        with WaveSink(temporary) as sink:
            sink.write(np.zeros(2), 24000)
        raise RuntimeError("render failed")

    assert not path.exists()
    assert list(tmp_path.iterdir()) == []
