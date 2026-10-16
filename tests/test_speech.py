from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from readio import api, speech
from readio.playback import play_audio


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        ("inflect:micro-v2/default", speech.VoiceSelector("inflect", "micro-v2", "default")),
        ("kokoro:v1.0/af_heart", speech.VoiceSelector("kokoro", "v1.0", "af_heart")),
        ("piper:en_US-amy-medium", speech.VoiceSelector("piper", "en_US-amy-medium", None)),
        ("af_heart", speech.VoiceSelector(None, None, "af_heart")),
        (None, speech.VoiceSelector()),
    ],
)
def test_parse_voice_selector(selector: str | None, expected: speech.VoiceSelector) -> None:
    assert speech.parse_voice_selector(selector) == expected


@pytest.mark.parametrize("selector", ["", ":voice", "engine:", "engine:target/"])
def test_parse_voice_selector_rejects_incomplete_values(selector: str) -> None:
    with pytest.raises(ValueError):
        speech.parse_voice_selector(selector)


def test_speak_delegates_direct_synthesis_and_plays_returned_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "Hello, how are you?"
    samples = np.arange(4, dtype=np.float32)
    rendered = SimpleNamespace(audio=samples, sample_rate=24000)
    selection = SimpleNamespace(voice="default", speaker=None)
    session = object()
    events: list[tuple[str, object]] = []

    def resolve_voice(request: object) -> tuple[object, tuple[object, ...]]:
        events.append(("resolve", request))
        return selection, ()

    @contextmanager
    def open_session(value: object):
        events.append(("open", value))
        yield session

    def synthesize(value: object, request: object) -> object:
        events.append(("synthesize", (value, request)))
        return rendered

    def play(audio: object, sample_rate: int) -> None:
        events.append(("play", (audio, sample_rate)))

    monkeypatch.setattr(speech.voicerender, "resolve_voice", resolve_voice)
    monkeypatch.setattr(speech.voicerender, "open_session", open_session)
    monkeypatch.setattr(speech.voicerender, "synthesize", synthesize)
    monkeypatch.setattr(speech, "play_audio", play)

    result = api.speak(
        text,
        voice="inflect:micro-v2/default",
        language="en",
        offline=True,
        refresh=True,
        engine_options={"temperature": "0.7"},
    )

    request = events[0][1]
    speech_request = events[2][1][1]
    assert request.engine == "inflect"
    assert request.target_id == "micro-v2"
    assert request.voice == "default"
    assert request.language == "en"
    assert request.offline is True
    assert request.refresh is True
    assert request.engine_options == {"temperature": "0.7"}
    assert speech_request.text is text
    assert speech_request.language == "en"
    assert events == [
        ("resolve", request),
        ("open", selection),
        ("synthesize", (session, speech_request)),
        ("play", (samples, 24000)),
    ]
    assert result is rendered


def test_speak_propagates_voicerender_failures_without_translation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class VoiceFailure(Exception):
        pass

    failure = VoiceFailure("engine unavailable")

    def fail(_request: object) -> object:
        raise failure

    monkeypatch.setattr(speech.voicerender, "resolve_voice", fail)
    with pytest.raises(VoiceFailure) as caught:
        speech.speak("Hello", voice="inflect:micro-v2/default")
    assert caught.value is failure


def test_runtime_speech_has_no_readio_engine_import() -> None:
    source = Path(speech.__file__).read_text(encoding="utf-8")
    assert "readio.engines" not in source
    assert "resolve_synthesis" not in source


def test_play_audio_uses_generic_sample_rate_and_channel_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []

    class Playback:
        def start(self, sample_rate: int, channels: int) -> None:
            calls.append(("start", sample_rate, channels))

        def submit(self, audio: np.ndarray) -> None:
            calls.append(("submit", audio))

        def drain(self) -> None:
            calls.append(("drain",))

        def close(self) -> None:
            calls.append(("close",))

    monkeypatch.setattr("readio.playback.SoundDevicePlayback", Playback)
    samples = np.ones((3, 2), dtype=np.float32)
    play_audio(samples, 44100)

    assert calls[0] == ("start", 44100, 2)
    assert np.array_equal(calls[1][1], samples)
    assert [call[0] for call in calls] == ["start", "submit", "drain", "close"]
