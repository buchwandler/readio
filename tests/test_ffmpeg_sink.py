from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from readio.errors import RenderError
from readio.wave import FFmpegAudioSink, build_ffmpeg_command


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


def _has_pair(command: list[str], option: str, value: str) -> bool:
    return any(command[index : index + 2] == [option, value] for index in range(len(command) - 1))


def test_build_ffmpeg_command_contains_streaming_m4a_arguments(tmp_path: Path):
    command = build_ffmpeg_command("ffmpeg", tmp_path / "episode.m4a", 24000, 2)
    assert command[:3] == ["ffmpeg", "-hide_banner", "-loglevel"]
    assert _has_pair(command, "-f", "f32le")
    assert _has_pair(command, "-ar", "24000")
    assert _has_pair(command, "-ac", "2")
    assert _has_pair(command, "-i", "pipe:0")
    assert _has_pair(command, "-c:a", "aac")
    assert _has_pair(command, "-b:a", "192k")
    assert _has_pair(command, "-f", "ipod")
    assert command[-1] == str(tmp_path / "episode.m4a")


def test_requested_m4a_bitrate_reaches_ffmpeg_command(tmp_path: Path):
    command = build_ffmpeg_command("ffmpeg", tmp_path / "episode.m4a", 24000, 2, bitrate="64k")
    assert _has_pair(command, "-b:a", "64k")


def test_opus_uses_libopus_and_requested_bitrate(tmp_path: Path):
    command = build_ffmpeg_command(
        "ffmpeg", tmp_path / "episode.opus", 24000, 1, "opus", bitrate="96k"
    )
    assert _has_pair(command, "-c:a", "libopus")
    assert _has_pair(command, "-b:a", "96k")
    assert _has_pair(command, "-f", "opus")
    assert command[-1] == str(tmp_path / "episode.opus")


def test_ffmpeg_sink_starts_on_first_chunk_and_streams_bytes(monkeypatch, tmp_path: Path):
    calls = []

    def popen(command, *, stdin, stderr):
        process = FakeProcess(stderr)
        calls.append((command, process))
        return process

    monkeypatch.setattr("readio.wave.subprocess.Popen", popen)
    sink = FFmpegAudioSink(tmp_path / "episode.m4a", "m4a", executable="ffmpeg", bitrate="128k")
    assert calls == []
    first = np.array([0.25, -0.5], dtype=np.float32)
    second = np.array([1.0], dtype=np.float32)
    sink.write(first, 24000)
    sink.write(second, 24000)
    process = calls[0][1]
    sink.close()

    assert len(calls) == 1
    assert _has_pair(calls[0][0], "-b:a", "128k")
    assert bytes(process.stdin.payload) == np.concatenate((first, second)).astype("<f4").tobytes()
    assert process.stdin.closed
    assert process.wait_calls == 1


def test_ffmpeg_sink_rejects_chunk_format_changes(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "readio.wave.subprocess.Popen",
        lambda command, *, stdin, stderr: FakeProcess(stderr),
    )
    with FFmpegAudioSink(tmp_path / "episode.opus", "opus", executable="ffmpeg") as sink:
        sink.write(np.zeros(2), 24000)
        with pytest.raises(ValueError, match="same sample rate and channel count"):
            sink.write(np.zeros((2, 2)), 22050)


def test_ffmpeg_sink_reports_encoder_failure_for_actual_format(monkeypatch, tmp_path: Path):
    def popen(command, *, stdin, stderr):
        stderr.write(b"invalid audio data\n")
        return FakeProcess(stderr, returncode=2)

    monkeypatch.setattr("readio.wave.subprocess.Popen", popen)
    sink = FFmpegAudioSink(tmp_path / "episode.opus", "opus", executable="ffmpeg")
    sink.write(np.zeros(2), 24000)
    with pytest.raises(RenderError, match="FFmpeg failed to encode OPUS: invalid audio data"):
        sink.close()


def test_ffmpeg_sink_does_not_mask_render_exception(monkeypatch, tmp_path: Path):
    processes = []

    def popen(command, *, stdin, stderr):
        process = FakeProcess(stderr)
        processes.append(process)
        return process

    monkeypatch.setattr("readio.wave.subprocess.Popen", popen)
    with (
        pytest.raises(ValueError, match="render failed"),
        FFmpegAudioSink(tmp_path / "episode.m4a", "m4a", executable="ffmpeg") as sink,
    ):
        sink.write(np.zeros(2), 24000)
        raise ValueError("render failed")
    assert processes[0].terminated


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required for the Opus integration test",
)
def test_opus_sink_produces_readable_opus_file(tmp_path: Path):
    output = tmp_path / "speech.opus"
    with FFmpegAudioSink(output, "opus", bitrate="64k") as sink:
        sink.write(np.zeros(4800, dtype=np.float32), 24000)

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert probe.stdout.strip() == "opus"
