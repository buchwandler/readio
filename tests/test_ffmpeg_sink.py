from pathlib import Path

import numpy as np
import pytest

from readio.errors import RenderError
from readio.wave import FFmpegM4ASink, build_ffmpeg_command


class FakeStdin:
    def __init__(self) -> None:
        self.payload = bytearray()
        self.closed = False

    def write(self, payload: bytes) -> int:
        self.payload.extend(payload)
        return len(payload)

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self, stderr: object, returncode: int = 0) -> None:
        self.stdin = FakeStdin()
        self.returncode = returncode
        self.stderr = stderr
        self.terminated = False
        self.wait_calls = 0

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.terminated = True


def test_build_ffmpeg_command_contains_streaming_m4a_arguments(tmp_path: Path):
    command = build_ffmpeg_command("ffmpeg", tmp_path / "episode.m4a", 24000, 2)
    assert command[:3] == ["ffmpeg", "-hide_banner", "-loglevel"]
    assert ["-f", "f32le"] == command[6:8]
    assert ["-ar", "24000"] == command[8:10]
    assert ["-ac", "2"] == command[10:12]
    assert ["-i", "pipe:0"] == command[12:14]
    assert ["-c:a", "aac"] == command[15:17]
    assert ["-f", "ipod"] == command[21:23]
    assert command[-1] == str(tmp_path / "episode.m4a")


def test_ffmpeg_sink_starts_on_first_chunk_and_streams_bytes(monkeypatch, tmp_path: Path):
    calls = []

    def popen(command, *, stdin, stderr):
        process = FakeProcess(stderr)
        calls.append((command, process))
        return process

    monkeypatch.setattr("readio.wave.subprocess.Popen", popen)
    sink = FFmpegM4ASink(tmp_path / "episode.m4a", executable="ffmpeg")
    assert calls == []
    first = np.array([0.25, -0.5], dtype=np.float32)
    second = np.array([1.0], dtype=np.float32)
    sink.write(first, 24000)
    sink.write(second, 24000)
    process = calls[0][1]
    sink.close()

    assert len(calls) == 1
    assert bytes(process.stdin.payload) == np.concatenate((first, second)).astype("<f4").tobytes()
    assert process.stdin.closed
    assert process.wait_calls == 1


def test_ffmpeg_sink_rejects_chunk_format_changes(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "readio.wave.subprocess.Popen",
        lambda command, *, stdin, stderr: FakeProcess(stderr),
    )
    with FFmpegM4ASink(tmp_path / "episode.m4a", executable="ffmpeg") as sink:
        sink.write(np.zeros(2), 24000)
        with pytest.raises(ValueError, match="same sample rate and channel count"):
            sink.write(np.zeros((2, 2)), 22050)


def test_ffmpeg_sink_reports_encoder_failure(monkeypatch, tmp_path: Path):
    def popen(command, *, stdin, stderr):
        stderr.write(b"invalid audio data\n")
        return FakeProcess(stderr, returncode=2)

    monkeypatch.setattr("readio.wave.subprocess.Popen", popen)
    sink = FFmpegM4ASink(tmp_path / "episode.m4a", executable="ffmpeg")
    sink.write(np.zeros(2), 24000)
    with pytest.raises(RenderError, match="invalid audio data"):
        sink.close()


def test_ffmpeg_sink_does_not_mask_render_exception(monkeypatch, tmp_path: Path):
    processes = []

    def popen(command, *, stdin, stderr):
        process = FakeProcess(stderr)
        processes.append(process)
        return process

    monkeypatch.setattr("readio.wave.subprocess.Popen", popen)
    with pytest.raises(ValueError, match="render failed"), FFmpegM4ASink(
        tmp_path / "episode.m4a", executable="ffmpeg"
    ) as sink:
        sink.write(np.zeros(2), 24000)
        raise ValueError("render failed")
    assert processes[0].terminated
