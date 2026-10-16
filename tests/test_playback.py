from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import ClassVar

import numpy as np
import pytest

from readio.playback import PlaybackError, SoundDevicePlayback, play_audio


class _OutputStream:
    instances: ClassVar[list[_OutputStream]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.started = False
        self.writes: list[np.ndarray] = []
        self.stopped = False
        self.closed = False
        self.__class__.instances.append(self)

    def start(self) -> None:
        self.started = True

    def write(self, audio: np.ndarray) -> None:
        self.writes.append(audio.copy())

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


def test_sounddevice_playback_writes_audio_and_closes_stream(monkeypatch) -> None:
    _OutputStream.instances.clear()
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(OutputStream=_OutputStream))
    player = SoundDevicePlayback(device="USB", queue_size=2).start(24000, 1)
    player.submit(np.ones(3, dtype=np.float32))
    player.submit(np.ones((2, 1), dtype=np.float32))
    player.drain()
    player.close()

    stream = _OutputStream.instances[0]
    assert stream.kwargs == {
        "samplerate": 24000,
        "channels": 1,
        "device": "USB",
        "dtype": "float32",
    }
    assert stream.started and stream.stopped and stream.closed
    assert [chunk.shape for chunk in stream.writes] == [(3, 1), (2, 1)]


def test_play_audio_plays_voice_render_arrays(monkeypatch) -> None:
    _OutputStream.instances.clear()
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(OutputStream=_OutputStream))
    samples = np.ones((4, 2), dtype=np.float32)

    play_audio(samples, 44100)

    stream = _OutputStream.instances[0]
    assert stream.kwargs["samplerate"] == 44100
    assert stream.kwargs["channels"] == 2
    assert [chunk.shape for chunk in stream.writes] == [(4, 2)]
    assert stream.closed


def test_playback_initialization_errors_are_playback_specific(monkeypatch) -> None:
    class BrokenStream:
        def __init__(self, **_kwargs: object) -> None:
            raise OSError("no output device")

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(OutputStream=BrokenStream))
    with pytest.raises(PlaybackError, match="sounddevice playback backend.*no output device"):
        SoundDevicePlayback().start(24000, 1)
