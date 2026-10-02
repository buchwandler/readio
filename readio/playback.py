"""Readio-owned audio-device playback using a bounded sounddevice queue."""

from __future__ import annotations

from queue import Queue
from threading import Thread
from typing import Any

import numpy as np


class PlaybackError(RuntimeError):
    """Raised when the playback backend cannot initialize or write audio."""


_END = object()


class SoundDevicePlayback:
    """Bounded producer/consumer playback backed directly by sounddevice."""

    def __init__(self, *, device: str | int | None = None, queue_size: int = 8) -> None:
        if isinstance(queue_size, bool) or not isinstance(queue_size, int) or queue_size <= 0:
            raise ValueError("playback queue_size must be a positive integer")
        self._device = device
        self._queue_size = queue_size
        self._stream: Any = None
        self._queue: Queue[np.ndarray | object] | None = None
        self._worker: Thread | None = None
        self._sample_rate: int | None = None
        self._channels: int | None = None
        self._error: BaseException | None = None
        self._closed = False

    def start(self, sample_rate: int, channels: int) -> SoundDevicePlayback:
        if self._closed:
            raise PlaybackError("playback backend is closed")
        if self._stream is not None:
            if sample_rate != self._sample_rate or channels != self._channels:
                raise PlaybackError("playback stream format cannot change after start")
            return self
        stream: Any = None
        try:
            import sounddevice as sd

            stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=channels,
                device=self._device,
                dtype="float32",
            )
            stream.start()
        except Exception as error:
            if stream is not None:
                try:
                    stream.close()
                except Exception as close_error:  # noqa: BLE001 - preserve backend cleanup failure
                    raise PlaybackError(
                        "could not initialize sounddevice playback backend; "
                        f"cleanup also failed: {close_error}"
                    ) from error
            raise PlaybackError(
                f"could not initialize sounddevice playback backend: {error}"
            ) from error

        self._stream = stream
        self._sample_rate = sample_rate
        self._channels = channels
        self._queue = Queue(maxsize=self._queue_size)
        self._worker = Thread(target=self._run, name="readio-playback", daemon=True)
        self._worker.start()
        return self

    def submit(self, audio: np.ndarray) -> None:
        if self._closed or self._queue is None or self._channels is None:
            raise PlaybackError("playback backend has not been started")
        self._raise_worker_error()
        array = np.asarray(audio, dtype=np.float32)
        if array.ndim == 1:
            if self._channels != 1:
                raise PlaybackError("mono audio does not match the playback channel count")
        elif array.ndim != 2 or array.shape[1] != self._channels:
            raise PlaybackError("audio chunk does not match the playback channel count")
        self._queue.put(array)
        self._raise_worker_error()

    def drain(self) -> None:
        if self._queue is None or self._worker is None:
            return
        self._queue.join()
        self._raise_worker_error()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        stream = self._stream
        try:
            if self._queue is not None and self._worker is not None:
                self._queue.put(_END)
                self._queue.join()
                self._worker.join()
            if stream is not None:
                try:
                    stream.stop()
                finally:
                    stream.close()
        except Exception as error:  # noqa: BLE001 - surface backend shutdown failures
            if self._error is None:
                self._error = error
        self._raise_worker_error()

    def _run(self) -> None:
        assert self._queue is not None
        while True:
            item = self._queue.get()
            try:
                if item is _END:
                    return
                if self._error is None:
                    assert self._stream is not None
                    audio = np.asarray(item, dtype=np.float32)
                    if audio.ndim == 1:
                        audio = audio.reshape((-1, 1))
                    self._stream.write(audio)
            except BaseException as error:  # noqa: BLE001 - keep the worker draining on every failure
                if self._error is None:
                    self._error = error
            finally:
                self._queue.task_done()

    def _raise_worker_error(self) -> None:
        if self._error is not None:
            raise PlaybackError(f"sounddevice playback failed: {self._error}") from self._error


__all__ = ["PlaybackError", "SoundDevicePlayback"]
