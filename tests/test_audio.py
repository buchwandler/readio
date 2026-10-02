from __future__ import annotations

import inspect
from typing import ClassVar

import numpy as np
import pytest

from readio.audio import (
    AudioSink,
    PlaybackSink,
    RenderProgress,
    RenderProgressCallback,
    RenderSummary,
)
from readio.config import ReaderSettings


class _Player:
    instances: ClassVar[list[_Player]] = []

    def __init__(self, *, device=None, queue_size=8):
        self.device = device
        self.queue_size = queue_size
        self.started = None
        self.submitted = []
        self.drained = False
        self.closed = False
        self.__class__.instances.append(self)

    def start(self, sample_rate, channels):
        self.started = (sample_rate, channels)
        return self

    def submit(self, audio):
        self.submitted.append(audio)

    def drain(self):
        self.drained = True

    def close(self):
        self.closed = True


def test_playback_sink_creates_one_player_and_drains(monkeypatch):
    from readio import playback

    _Player.instances.clear()
    monkeypatch.setattr(playback, "SoundDevicePlayback", _Player)
    sink = PlaybackSink(ReaderSettings(queue_size=4, device="USB"))
    sink.write(np.ones(3), 24000)
    sink.write(np.ones((2, 1)), 24000)
    sink.finish()
    sink.close()
    sink.close()

    assert len(_Player.instances) == 1
    player = _Player.instances[0]
    assert (player.device, player.queue_size) == ("USB", 4)
    assert player.started == (24000, 1)
    assert len(player.submitted) == 2
    assert player.drained and player.closed


def test_playback_sink_rejects_format_change(monkeypatch):
    from readio import playback

    _Player.instances.clear()
    monkeypatch.setattr(playback, "SoundDevicePlayback", _Player)
    sink = PlaybackSink(ReaderSettings())
    sink.write(np.ones(2), 24000)

    with pytest.raises(ValueError, match="same sample rate and channel count"):
        sink.write(np.ones(2), 22050)

    sink.close()
    assert len(_Player.instances[0].submitted) == 1


def test_audio_contract_exports_only_sink_and_summary_primitives() -> None:
    from readio import audio

    assert audio.AudioSink is AudioSink
    assert audio.PlaybackSink is PlaybackSink
    assert audio.RenderProgress is RenderProgress
    assert audio.RenderProgressCallback is RenderProgressCallback
    assert audio.RenderSummary is RenderSummary
    assert not hasattr(audio, "render_prepared")


def test_soundfile_sink_matches_audio_sink_write_contract() -> None:
    from readio.wave import SoundFileSink

    assert list(inspect.signature(SoundFileSink.write).parameters) == [
        "self",
        "audio",
        "sample_rate",
    ]
