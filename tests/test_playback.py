from __future__ import annotations

import builtins
import sys
from types import SimpleNamespace
from typing import ClassVar

import numpy as np
import pytest

from readio.audio import PlaybackSink
from readio.config import ReaderSettings
from readio.playback import PlaybackError, SoundDevicePlayback


class _OutputStream:
    instances: ClassVar[list[_OutputStream]] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.writes = []
        self.stopped = False
        self.closed = False
        self.__class__.instances.append(self)

    def start(self):
        self.started = True

    def write(self, audio):
        self.writes.append(audio.copy())

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


def test_sounddevice_playback_owns_stream_and_drains_queue(monkeypatch):
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
    assert player._queue is not None and player._queue.maxsize == 2


def test_playback_sink_does_not_import_pykokoro(monkeypatch):
    _OutputStream.instances.clear()
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(OutputStream=_OutputStream))
    original_import = builtins.__import__

    def reject_pykokoro(name, *args, **kwargs):
        if name.startswith("pykokoro"):
            raise AssertionError("playback imported PyKokoro")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_pykokoro)
    sink = PlaybackSink(ReaderSettings(queue_size=2))
    sink.write(np.ones(4, dtype=np.float32), 24000)
    sink.finish()
    sink.close()

    assert len(_OutputStream.instances) == 1


def test_sounddevice_initialization_errors_are_playback_specific(monkeypatch):
    class BrokenStream:
        def __init__(self, **_kwargs):
            raise OSError("no output device")

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(OutputStream=BrokenStream))

    with pytest.raises(PlaybackError, match="sounddevice playback backend.*no output device"):
        SoundDevicePlayback().start(24000, 1)


def test_sounddevice_start_failure_closes_partial_stream(monkeypatch):
    class BrokenStartStream(_OutputStream):
        def start(self):
            raise OSError("device failed to start")

    _OutputStream.instances.clear()
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(OutputStream=BrokenStartStream))

    with pytest.raises(PlaybackError, match="device failed to start"):
        SoundDevicePlayback().start(24000, 1)

    assert _OutputStream.instances[0].closed


def test_sounddevice_write_errors_surface_on_drain(monkeypatch):
    class BrokenWriteStream(_OutputStream):
        def write(self, _audio):
            raise OSError("write failed")

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(OutputStream=BrokenWriteStream))
    player = SoundDevicePlayback().start(24000, 1)
    player.submit(np.ones(3, dtype=np.float32))

    with pytest.raises(PlaybackError, match="sounddevice playback failed.*write failed"):
        player.drain()
    with pytest.raises(PlaybackError, match="sounddevice playback failed.*write failed"):
        player.close()
