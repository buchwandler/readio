from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from renderplan import RenderProfile, RoleProfile

from readio import speech
from readio.api.types import AudioBuffer, SpeakRequest, SpeakResult
from readio.errors import (
    WorkflowReplanError,
    WorkflowResolutionError,
    WorkflowStageError,
)

_SEGMENT_ID = "a" * 64


def test_parse_voice_selector() -> None:
    selector = speech.parse_voice_selector("kokoro:kokoro-v1/af_heart")

    assert selector.engine == "kokoro"
    assert selector.target_id == "kokoro-v1"
    assert selector.voice == "af_heart"


def _report(status: str, *, replan: tuple[Any, ...] = (), checks: tuple[Any, ...] = ()) -> Any:
    return SimpleNamespace(status=status, replan=replan, checks=checks)


def _install_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    preflights: list[Any] | None = None,
    repeated_replan: bool = False,
) -> dict[str, Any]:
    import readio.integrations.audiocompose as compose_adapter
    import readio.integrations.utterplan as plan_adapter
    import readio.integrations.voicerender as render_adapter

    monkeypatch.chdir(tmp_path)
    calls: list[Any] = []
    progress: list[Any] = []
    profile = SimpleNamespace(
        profile_id="profile-1",
        default_role="narrator",
        roles={"narrator": object()},
    )
    flow = object()
    plan1 = SimpleNamespace(plan_id="render-plan-1")
    plan2 = SimpleNamespace(plan_id="render-plan-1" if repeated_replan else "render-plan-2")
    reports = list(
        preflights
        or [
            _report(
                "replan",
                replan=(
                    SimpleNamespace(
                        segment_number=1,
                        segment_id=_SEGMENT_ID,
                        metric="max_words",
                        measured=120,
                        maximum=100,
                    ),
                ),
            ),
            _report("ok"),
        ]
    )
    compiled = SimpleNamespace(
        plan=flow,
        diagnostics=(SimpleNamespace(message="compiler note"),),
    )
    speech_buffer = SimpleNamespace(
        audio=np.array([0.25, -0.25], dtype=np.float32),
        sample_rate=24000,
        warnings=("render note",),
        word_timings=(
            SimpleNamespace(
                text="Hello",
                char_start=0,
                char_end=5,
                start_sample=0,
                end_sample=2,
            ),
        ),
        metadata={"source": "test"},
    )
    rendered_result = SimpleNamespace(
        rendered=(SimpleNamespace(number=1, segment_id=_SEGMENT_ID, speech=speech_buffer),),
        warnings=(),
        error=None,
        selection_complete=True,
        error_segment_number=None,
        error_segment_id=None,
        fresh_preflight=None,
    )
    composed_audio = np.array([0.5, -0.5, 0.0], dtype=np.float32)

    def emit(callback: Any, kind: str) -> None:
        if callback is not None:
            callback(
                SimpleNamespace(
                    kind=kind,
                    current=1,
                    total=1,
                    details={"segment_number": 1, "segment_id": _SEGMENT_ID},
                )
            )

    def compile_document(text: str, **options: Any) -> Any:
        calls.append(("compile", text, options["input_format"], options["config"].language))
        emit(options.get("on_progress"), "compile.finished")
        return compiled

    def create_profile(**options: Any) -> Any:
        calls.append(("profile", options))
        return profile

    def build_render_plan(
        actual_flow: Any,
        actual_profile: Any,
        *,
        previous_plan: Any | None = None,
        preflight: Any | None = None,
    ) -> Any:
        calls.append(("build", actual_flow, actual_profile, previous_plan, preflight))
        if previous_plan is None:
            return plan1
        return plan1 if repeated_replan else plan2

    def preflight_render_plan(plan: Any, *, output: Any, on_event: Any) -> Any:
        calls.append(("preflight", plan, output))
        emit(on_event, "preflight.checked")
        if reports:
            return reports.pop(0)
        return _report("replan")

    def render_memory(plan: Any, *, preflight: Any, on_event: Any) -> Any:
        calls.append(("render", plan, preflight))
        emit(on_event, "render.finished")
        return rendered_result

    def adapt_buffer(
        audio: Any,
        sample_rate: int,
        *,
        word_timings: Any,
        metadata: Any,
    ) -> Any:
        calls.append(("buffer", audio, sample_rate, word_timings, metadata))
        return SimpleNamespace(audio=audio, sample_rate=sample_rate)

    def compose_memory(plan: Any, buffers: Any, *, on_progress: Any) -> Any:
        calls.append(("compose", plan, buffers))
        emit(on_progress, "compose.finished")
        return SimpleNamespace(audio=composed_audio, sample_rate=24000, warnings=("compose note",))

    monkeypatch.setattr(
        plan_adapter,
        "planner_config",
        lambda language: SimpleNamespace(language=language),
    )
    monkeypatch.setattr(plan_adapter, "compile_document", compile_document)
    monkeypatch.setattr(plan_adapter, "build_render_plan", build_render_plan)
    monkeypatch.setattr(render_adapter, "create_render_profile", create_profile)
    monkeypatch.setattr(render_adapter, "preflight_render_plan", preflight_render_plan)
    monkeypatch.setattr(render_adapter, "render_renderplan_memory", render_memory)
    monkeypatch.setattr(compose_adapter, "rendered_audio_buffer", adapt_buffer)
    monkeypatch.setattr(compose_adapter, "compose_renderplan_memory", compose_memory)
    return {
        "calls": calls,
        "progress": progress,
        "profile": profile,
        "flow": flow,
        "plan1": plan1,
        "plan2": plan2,
        "rendered": rendered_result,
        "audio": composed_audio,
    }


def test_speak_request_uses_semantic_in_memory_pipeline_and_bounded_replan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    setup = _install_pipeline(monkeypatch, tmp_path)
    request = SpeakRequest(
        text="Hello, semantic world.",
        engine="kokoro",
        target="kokoro-v1",
        voice="af_heart",
        language="en-US",
    )

    result = speech.speak_request(request, on_progress=setup["progress"].append)

    assert [call[0] for call in setup["calls"]] == [
        "compile",
        "profile",
        "build",
        "preflight",
        "build",
        "preflight",
        "render",
        "buffer",
        "compose",
    ]
    assert setup["calls"][0][1:4] == (request.text, "plain", "en-US")
    assert setup["calls"][3][2] is None
    assert setup["calls"][5][2] is None
    assert setup["calls"][4][3] is setup["plan1"]
    assert setup["calls"][4][4].status == "replan"
    assert setup["calls"][6][1] is setup["plan2"]
    assert setup["calls"][7][3] == (
        {
            "text": "Hello",
            "char_start": 0,
            "char_end": 5,
            "start_sample": 0,
            "end_sample": 2,
        },
    )
    assert result.profile_id == "profile-1"
    assert isinstance(result.audio, AudioBuffer)
    assert result.audio.sample_rate == 24000
    assert len(result.audio) == 3
    assert result.audio.channels == 1
    assert result.audio.audio.tolist() == [0.5, -0.5, 0.0]
    assert result.warnings == ("compiler note", "render note", "compose note")
    assert result.elapsed_seconds is not None
    assert {event.stage for event in setup["progress"]} == {"compile", "render", "compose"}
    assert not list(tmp_path.iterdir()), "semantic speech must not create pipeline files"


def test_direct_voice_profile_creation_and_stored_profile_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import readio.integrations.voicerender as render_adapter

    created: list[dict[str, Any]] = []
    direct_profile = SimpleNamespace(profile_id="direct-profile")
    monkeypatch.setattr(
        render_adapter,
        "create_render_profile",
        lambda **options: created.append(options) or direct_profile,
    )
    direct = speech._profile_for_request(
        SpeakRequest(
            text="hello",
            voice="kokoro:kokoro-v1/af_heart",
            language="en-GB",
            speaker=3,
            engine_options={"speed": 1.2},
        )
    )
    assert direct is direct_profile
    assert created[0]["engine"] == "kokoro"
    assert created[0]["target_id"] == "kokoro-v1"
    assert created[0]["voice"] == "af_heart"
    assert created[0]["language"] == "en-GB"
    assert created[0]["speaker"] == 3
    assert created[0]["options"] == {"speed": 1.2}

    stored = RenderProfile(
        roles={
            "narrator": RoleProfile(
                language="en-US",
                binding={"engine": "kokoro", "target_id": "kokoro-v1", "voice": "a"},
            ),
            "guide": RoleProfile(
                language="fr-FR",
                binding={
                    "engine": "kokoro",
                    "target_id": "kokoro-v1",
                    "engine_options": {"speed": 1.0},
                },
            ),
        },
        default_role="narrator",
    ).with_identity()
    monkeypatch.setattr(render_adapter, "load_render_profile", lambda _path: stored)
    resolved = speech._profile_for_request(
        SpeakRequest(
            text="hello",
            profile=tmp_path / "profile.toml",
            role="guide",
            language="de-DE",
            speaker="speaker-a",
            engine_options={"speed": 1.3},
        )
    )

    assert resolved.default_role == "guide"
    assert resolved.roles["guide"].language == "de-DE"
    assert resolved.roles["guide"].binding["speaker"] == "speaker-a"
    assert resolved.roles["guide"].binding["engine_options"] == {"speed": 1.3}
    assert stored.default_role == "narrator"
    assert stored.roles["guide"].language == "fr-FR"
    assert resolved.profile_id != stored.profile_id
    assert not list(tmp_path.iterdir()), "loading and adapting profiles must not persist artifacts"


def test_profile_and_direct_voice_arguments_are_mutually_exclusive() -> None:
    with pytest.raises(WorkflowResolutionError, match="mutually exclusive"):
        speech._profile_for_request(
            SpeakRequest(text="hello", profile=Path("profile.toml"), engine="kokoro")
        )


def test_preflight_rejection_keeps_segment_identity_and_measurements(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    failure = SimpleNamespace(
        status="error",
        segment_number=4,
        segment_id=_SEGMENT_ID,
        metric="max_words",
        measured=150,
        maximum=100,
        error="capacity exceeded",
    )
    setup = _install_pipeline(
        monkeypatch, tmp_path, preflights=[_report("reject", checks=(failure,))]
    )

    with pytest.raises(WorkflowStageError) as caught:
        speech.speak_request(SpeakRequest(text="hello", engine="kokoro", target="kokoro-v1"))

    error = caught.value
    assert error.stage == "preflight"
    assert error.package == "voicerender"
    assert error.selector == f"speech 4 ({_SEGMENT_ID})"
    assert "max_words 150 / 100" in str(error)
    assert error.details["checks"]
    assert isinstance(error.cause, RuntimeError)
    assert not any(call[0] in {"render", "compose"} for call in setup["calls"])


def test_render_failure_preserves_engine_code_and_occurrence_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import readio.integrations.voicerender as render_adapter

    setup = _install_pipeline(monkeypatch, tmp_path, preflights=[_report("ok")])
    failure = SimpleNamespace(
        rendered=(),
        warnings=(),
        error="engine could not synthesize occurrence",
        error_code="engine.render_failed",
        error_segment_number=3,
        error_segment_id=_SEGMENT_ID,
        error_engine="kokoro",
        error_type="EngineError",
        error_diagnostic={"reason": "unavailable"},
        selection_complete=False,
        fresh_preflight=None,
    )
    monkeypatch.setattr(
        render_adapter, "render_renderplan_memory", lambda *_args, **_kwargs: failure
    )

    with pytest.raises(
        WorkflowStageError, match="engine could not synthesize occurrence"
    ) as caught:
        speech.speak_request(SpeakRequest(text="hello", engine="kokoro", target="kokoro-v1"))

    error = caught.value
    assert error.stage == "render"
    assert error.code == "engine.render_failed"
    assert error.selector == f"speech 3 ({_SEGMENT_ID})"
    assert error.details["error_engine"] == "kokoro"
    assert error.details["error_diagnostic"] == {"reason": "unavailable"}
    assert isinstance(error.cause, RuntimeError)
    assert not any(call[0] == "compose" for call in setup["calls"])


def test_replan_rejects_repeated_plan_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    setup = _install_pipeline(monkeypatch, tmp_path, repeated_replan=True)

    with pytest.raises(
        WorkflowReplanError, match="did not produce a new RenderPlan identity"
    ) as caught:
        speech.speak_request(SpeakRequest(text="hello", engine="kokoro", target="kokoro-v1"))

    assert caught.value.details["previous_render_plan_id"] == "render-plan-1"
    assert caught.value.details["replanned_render_plan_id"] == "render-plan-1"
    assert not any(call[0] == "render" for call in setup["calls"])


def test_replan_loop_is_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    setup = _install_pipeline(monkeypatch, tmp_path, repeated_replan=False)
    next_id = 2

    import readio.integrations.utterplan as plan_adapter

    def new_plan(
        _flow: Any, _profile: Any, *, previous_plan: Any = None, preflight: Any = None
    ) -> Any:
        nonlocal next_id
        setup["calls"].append(("bounded-build", previous_plan, preflight))
        if previous_plan is None:
            return setup["plan1"]
        plan = SimpleNamespace(plan_id=f"render-plan-{next_id}")
        next_id += 1
        return plan

    import readio.integrations.voicerender as render_adapter

    def always_replan(plan: Any, *, output: Any, **_options: Any) -> Any:
        setup["calls"].append(("bounded-preflight", plan, output))
        return _report(
            "replan",
            replan=(
                SimpleNamespace(
                    segment_number=2,
                    segment_id=_SEGMENT_ID,
                    metric="max_words",
                    measured=101,
                    maximum=100,
                ),
            ),
        )

    monkeypatch.setattr(plan_adapter, "build_render_plan", new_plan)
    monkeypatch.setattr(render_adapter, "preflight_render_plan", always_replan)
    monkeypatch.setattr(speech, "MAX_SPEAK_REPLANS", 2)

    with pytest.raises(WorkflowReplanError, match="more than 2 replans") as caught:
        speech.speak_request(SpeakRequest(text="hello", engine="kokoro", target="kokoro-v1"))

    assert caught.value.details["replan_count"] == 2
    assert len([call for call in setup["calls"] if call[0] == "bounded-preflight"]) == 3
    assert f"speech 2 ({_SEGMENT_ID})" in str(caught.value)


def test_readio_speak_returns_audio_and_playback_is_optional(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service_module = importlib.import_module("readio.api.service")
    monkeypatch.chdir(tmp_path)
    result = SpeakResult(
        audio=AudioBuffer(audio=np.array([0.2, -0.2], dtype=np.float32), sample_rate=22050),
        profile_id="profile-1",
    )
    calls: list[Any] = []
    monkeypatch.setattr(service_module, "_speak_request", lambda _request, **_options: result)
    monkeypatch.setattr(
        service_module,
        "play_audio",
        lambda audio, rate: calls.append((audio.copy(), rate)),
    )
    api = service_module.Readio()
    request = SpeakRequest(text="Hello", engine="kokoro", target="kokoro-v1")

    assert api.speak(request) is result
    assert calls[0][1] == 22050
    assert calls[0][0].tolist() == pytest.approx([0.2, -0.2])
    assert api.speak(request, play=False) is result
    assert len(calls) == 1
    assert not list(tmp_path.iterdir()), "playback must not require pipeline files"


def test_runtime_speech_has_no_readio_engine_import() -> None:
    source = Path(speech.__file__).read_text(encoding="utf-8")
    assert "readio.engines" not in source


def test_play_audio_uses_generic_mono_output_stream(monkeypatch: pytest.MonkeyPatch) -> None:

    captured: dict[str, Any] = {}

    class OutputStream:
        def __init__(
            self,
            *,
            samplerate: int,
            channels: int,
            device: str | int | None,
            dtype: str,
        ) -> None:
            captured["samplerate"] = samplerate
            captured["channels"] = channels
            captured["device"] = device
            captured["dtype"] = dtype

        def start(self) -> None:
            captured["started"] = True

        def write(self, audio: np.ndarray) -> None:
            captured["audio"] = audio.copy()

        def stop(self) -> None:
            captured["stopped"] = True

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(OutputStream=OutputStream))
    audio = np.array([0.25, -0.5], dtype=np.float32)
    speech.play_audio(audio, 22050)

    assert captured["samplerate"] == 22050
    assert captured["channels"] == 1
    assert captured["dtype"] == "float32"
    assert captured["audio"].reshape(-1).tolist() == [0.25, -0.5]
