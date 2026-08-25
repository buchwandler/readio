from dataclasses import dataclass
from typing import ClassVar

import numpy as np
import pytest

from readio.audio import PlaybackSink, RenderProgress, render_prepared
from readio.config import ReaderConfig


@dataclass
class Result:
    audio: np.ndarray
    sample_rate: int
    markers: list[dict[str, object]]
    document_metadata: dict[str, object]
    released: bool = False

    def release_audio(self) -> None:
        self.released = True


class Prepared:
    document_metadata: ClassVar = {"title": "Episode"}

    def __init__(self, *results: Result) -> None:
        self.results = results
        self.units = tuple(range(len(results)))
        self.indices = None

    def render(self, *, indices=None):
        self.indices = indices
        yield from self.results


class Sink:
    def __init__(self, fail: bool = False) -> None:
        self.chunks = []
        self.fail = fail

    def write(self, audio, sample_rate):
        if self.fail:
            raise RuntimeError("sink failed")
        self.chunks.append((audio.copy(), sample_rate))

    def close(self):
        pass


def test_render_prepared_streams_chunks_and_aggregates_metadata_and_markers():
    first = Result(np.ones(3), 24000, [{"name": "intro", "sample_offset": 1}], {})
    second = Result(np.ones(2), 24000, [{"name": "topic", "sample_offset": 0}], {})
    prepared = Prepared(first, second)
    sink = Sink()

    summary = render_prepared(prepared, sink)

    assert [len(audio) for audio, _ in sink.chunks] == [3, 2]
    assert summary.sample_rate == 24000
    assert summary.sample_count == 5
    assert summary.channels == 1
    assert summary.document_metadata == {"title": "Episode"}
    assert summary.markers == (
        {"name": "intro", "sample_offset": 1},
        {"name": "topic", "sample_offset": 3},
    )
    assert first.released and second.released


def test_render_prepared_emits_initial_and_post_write_progress():
    first = Result(np.ones(3), 24000, [], {})
    second = Result(np.ones(2), 24000, [], {})
    events: list[RenderProgress] = []

    render_prepared(Prepared(first, second), Sink(), on_progress=events.append)

    assert events == [
        RenderProgress(0, 2, 0, 0),
        RenderProgress(1, 2, 3, 24000),
        RenderProgress(2, 2, 5, 24000),
    ]


def test_render_prepared_progress_uses_selected_total():
    events: list[RenderProgress] = []
    render_prepared(
        Prepared(Result(np.ones(2), 24000, [], {})),
        Sink(),
        indices=(4,),
        on_progress=events.append,
    )

    assert events[0].total_units == 1
    assert events[-1].completed_units == 1


def test_render_prepared_does_not_report_failed_sink_write():
    events: list[RenderProgress] = []
    result = Result(np.ones(3), 24000, [], {})

    with pytest.raises(RuntimeError, match="sink failed"):
        render_prepared(Prepared(result), Sink(fail=True), on_progress=events.append)

    assert events == [RenderProgress(0, 1, 0, 0)]
    assert result.released


def test_render_prepared_releases_result_when_sink_fails():
    result = Result(np.ones(3), 24000, [], {})

    with pytest.raises(RuntimeError, match="sink failed"):
        render_prepared(Prepared(result), Sink(fail=True))

    assert result.released


def test_playback_sink_creates_one_player_and_drains(monkeypatch):
    class Player:
        instances: ClassVar = []

        def __init__(self, sample_rate, **kwargs):
            self.sample_rate = sample_rate
            self.kwargs = kwargs
            self.submitted = []
            self.drained = False
            self.closed = False
            self.__class__.instances.append(self)

        def start(self):
            return self

        def submit(self, audio):
            self.submitted.append(audio)

        def drain(self):
            self.drained = True

        def close(self):
            self.closed = True

    import pykokoro.playback

    monkeypatch.setattr(pykokoro.playback, "SoundDevicePlayer", Player)
    sink = PlaybackSink(ReaderConfig(queue_size=4, device="USB"))
    sink.write(np.ones(3), 24000)
    sink.write(np.ones((2, 1)), 24000)
    sink.finish()
    sink.close()

    assert len(Player.instances) == 1
    player = Player.instances[0]
    assert player.kwargs == {"device": "USB", "queue_size": 4, "channels": 1}
    assert len(player.submitted) == 2
    assert player.drained
    assert player.closed


def test_playback_sink_rejects_format_change(monkeypatch):
    class Player:
        def __init__(self, sample_rate, **kwargs):
            pass

        def start(self):
            return self

        def submit(self, audio):
            pass

        def close(self):
            pass

    import pykokoro.playback

    monkeypatch.setattr(pykokoro.playback, "SoundDevicePlayer", Player)
    sink = PlaybackSink(ReaderConfig())
    sink.write(np.ones(2), 24000)

    with pytest.raises(ValueError, match="same sample rate and channel count"):
        sink.write(np.ones(2), 22050)

    sink.close()
