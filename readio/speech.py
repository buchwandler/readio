"""Semantic, in-memory speech workflows built from Readio's specialist APIs."""

from __future__ import annotations

import time
import wave
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from readio.api.types import SpeakRequest, SpeakResult
from readio.errors import (
    OutputCollisionError,
    WorkflowReplanError,
    WorkflowResolutionError,
    WorkflowStageError,
    WorkflowStateError,
)
from readio.playback import play_audio
from readio.voice_selector import VoiceSelector, parse_voice_selector

MAX_SPEAK_REPLANS = 8
WorkflowProgressHandler = Callable[[Any], None] | None


def _code(error: Exception) -> str | None:
    value = getattr(error, "code", None)
    return value if isinstance(value, str) else None


def _profile_for_request(request: SpeakRequest) -> Any:

    from readio.integrations import voicerender as voicerender_integration

    has_direct_voice = any(
        value is not None for value in (request.engine, request.target, request.voice)
    )
    if request.profile is not None:
        if has_direct_voice:
            raise WorkflowResolutionError(
                "profile is mutually exclusive with direct engine, target, and voice arguments"
            )
        try:
            profile = voicerender_integration.load_render_profile(request.profile)
        except Exception as error:
            raise WorkflowResolutionError(
                f"cannot load RenderProfile {request.profile}: {error}", cause=error
            ) from error
        role_name = request.role or profile.default_role
        if role_name not in profile.roles:
            raise WorkflowResolutionError(
                f"RenderProfile has no role {role_name!r}",
                details={"profile": str(request.profile), "role": role_name},
            )
        role_profile = profile.roles[role_name]
        binding = role_profile.binding
        if not isinstance(binding, Mapping):
            raise WorkflowResolutionError(
                f"RenderProfile role {role_name!r} has an invalid binding"
            )
        updated_binding = dict(binding)
        changed = role_name != profile.default_role
        if request.engine_options:
            base_options = updated_binding.get("engine_options", updated_binding.get("options", {}))
            if not isinstance(base_options, Mapping):
                raise WorkflowResolutionError(
                    f"RenderProfile role {role_name!r} has invalid engine options"
                )
            options = dict(base_options)
            options.update(request.engine_options)
            updated_binding["engine_options"] = options
            changed = True
        if request.speaker is not None:
            updated_binding["speaker"] = request.speaker
            changed = True
        language = request.language or role_profile.language
        if language != role_profile.language:
            changed = True
        if changed:
            updated_role = replace(
                role_profile,
                language=language,
                binding=updated_binding,
            )
            roles = dict(profile.roles)
            roles[role_name] = updated_role
            profile = replace(
                profile,
                roles=roles,
                default_role=role_name,
                profile_id=None,
            ).with_identity()
        return profile

    selector = parse_voice_selector(request.voice)
    engine = request.engine or selector.engine
    target_id = request.target or selector.target_id
    if not engine or not target_id:
        raise WorkflowResolutionError(
            "direct voice speech requires an engine and target, or a fully qualified voice selector"
        )
    try:
        return voicerender_integration.create_render_profile(
            engine=engine,
            target_id=target_id,
            language=request.language or "en-US",
            role=request.role or "narrator",
            voice=selector.voice,
            speaker=request.speaker,
            options=dict(request.engine_options),
            offline=request.offline,
            refresh=request.refresh,
        )
    except Exception as error:
        raise WorkflowResolutionError(
            f"cannot create an in-memory RenderProfile: {error}",
            cause=error,
            details={"engine": engine, "target": target_id, "voice": selector.voice},
        ) from error


def _profile_id(profile: Any) -> str:
    profile_id = getattr(profile, "profile_id", None)
    if isinstance(profile_id, str) and profile_id:
        return profile_id
    return profile.computed_id()


def _plan_id(plan: Any) -> str:
    plan_id = getattr(plan, "plan_id", None)
    return plan_id if isinstance(plan_id, str) and plan_id else plan.computed_id()


def _preflight_details(report: Any) -> dict[str, object]:
    to_dict = getattr(report, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
        if isinstance(value, Mapping):
            return {str(key): item for key, item in value.items()}
    requests = tuple(
        item.to_dict() if callable(getattr(item, "to_dict", None)) else item
        for item in getattr(report, "replan", ())
    )
    checks = tuple(
        item.to_dict() if callable(getattr(item, "to_dict", None)) else item
        for item in getattr(report, "checks", ())
    )
    return {"status": getattr(report, "status", None), "replan": requests, "checks": checks}


def _replan_summary(report: Any) -> tuple[str, ...]:
    summaries: list[str] = []
    for item in getattr(report, "replan", ()):
        number = getattr(item, "segment_number", "?")
        segment_id = getattr(item, "segment_id", "unknown")
        metric = getattr(item, "metric", None) or "measurement"
        measured = getattr(item, "measured", None)
        maximum = getattr(item, "maximum", None)
        summaries.append(f"speech {number} ({segment_id}): {metric} {measured} exceeds {maximum}")
    return tuple(summaries)


def _check_summary(report: Any) -> tuple[str, ...]:
    summaries: list[str] = []
    for item in getattr(report, "checks", ()):
        if getattr(item, "status", None) == "ok":
            continue
        number = getattr(item, "segment_number", "?")
        segment_id = getattr(item, "segment_id", "unknown")
        metric = getattr(item, "metric", None) or "measurement"
        measured = getattr(item, "measured", None)
        maximum = getattr(item, "maximum", None)
        detail = getattr(item, "error", None)
        summaries.append(
            f"speech {number} ({segment_id}): {metric} {measured} / {maximum}"
            + (f" ({detail})" if detail else "")
        )
    return tuple(summaries)


def _diagnostic_message(item: Any) -> str:
    message = getattr(item, "message", None)
    return message if isinstance(message, str) else str(item)


def _word_timing(item: Any) -> Mapping[str, object]:
    if isinstance(item, Mapping):
        return dict(item)
    to_dict = getattr(item, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
        if isinstance(value, Mapping):
            return dict(value)
    return {
        name: getattr(item, name)
        for name in ("text", "char_start", "char_end", "start_sample", "end_sample")
        if hasattr(item, name)
    }


def speak_request(
    request: SpeakRequest,
    *,
    on_progress: WorkflowProgressHandler = None,
) -> SpeakResult:
    """Run UtterPlan, VoiceRender, and AudioCompose entirely in memory."""
    from readio.api.types import AudioBuffer, SpeakResult
    from readio.integrations import audiocompose as audiocompose_integration
    from readio.integrations import utterplan as utterplan_integration
    from readio.integrations import voicerender as voicerender_integration
    from readio.progress import normalize_workflow_progress

    started = time.perf_counter()
    language = request.language or "en-US"
    compile_progress = normalize_workflow_progress("compile", "utterplan", on_progress)
    try:
        compiled = utterplan_integration.compile_document(
            request.text,
            input_format="plain",
            config=utterplan_integration.planner_config(language),
            on_progress=compile_progress,
        )
    except Exception as error:
        raise WorkflowStageError(
            "compile",
            str(error),
            package="utterplan",
            code=_code(error),
            cause=error,
        ) from error

    try:
        profile = _profile_for_request(request)
    except WorkflowResolutionError:
        raise
    except Exception as error:
        raise WorkflowResolutionError(
            f"cannot resolve a RenderProfile: {error}", cause=error
        ) from error
    profile_id = _profile_id(profile)
    flow_plan = compiled.plan
    try:
        render_plan = utterplan_integration.build_render_plan(flow_plan, profile)
    except Exception as error:
        raise WorkflowStageError(
            "render_plan",
            str(error),
            package="utterplan",
            variant=profile_id,
            code=_code(error),
            cause=error,
        ) from error

    seen_plan_ids: set[str] = set()
    replan_count = 0
    while True:
        current_plan_id = _plan_id(render_plan)
        if current_plan_id in seen_plan_ids:
            raise WorkflowReplanError(
                f"UtterPlan repeated RenderPlan identity {current_plan_id} during replanning",
                details={"render_plan_id": current_plan_id},
            )
        seen_plan_ids.add(current_plan_id)
        try:
            preflight = voicerender_integration.preflight_render_plan(
                render_plan,
                output=None,
                on_event=normalize_workflow_progress("render", "voicerender", on_progress),
            )
        except Exception as error:
            raise WorkflowStageError(
                "preflight",
                str(error),
                package="voicerender",
                variant=profile_id,
                code=_code(error),
                cause=error,
            ) from error

        status = getattr(preflight, "status", None)
        if status == "ok":
            break
        details = _preflight_details(preflight)
        summary = _replan_summary(preflight)
        if status == "reject":
            failures = _check_summary(preflight)
            message = "; ".join(failures) or "VoiceRender rejected the plan without a check detail"
            cause = RuntimeError(message)
            selector = failures[0].split(":", 1)[0] if failures else None
            raise WorkflowStageError(
                "preflight",
                message,
                package="voicerender",
                variant=profile_id,
                selector=selector,
                code="voicerender.preflight.reject",
                cause=cause,
                details=details,
            ) from cause
        if status != "replan":
            raise WorkflowStateError(
                f"VoiceRender returned an unknown preflight status {status!r}",
                details=details,
            )
        if replan_count >= MAX_SPEAK_REPLANS:
            raise WorkflowReplanError(
                f"VoiceRender requested more than {MAX_SPEAK_REPLANS} replans: "
                + ("; ".join(summary) or "no segment detail was supplied"),
                details={**details, "replan_count": replan_count},
            )
        try:
            refined_plan = utterplan_integration.build_render_plan(
                flow_plan,
                profile,
                previous_plan=render_plan,
                preflight=preflight,
            )
        except Exception as error:
            raise WorkflowStageError(
                "render_plan",
                str(error),
                package="utterplan",
                variant=profile_id,
                selector="; ".join(summary) or None,
                code=_code(error),
                cause=error,
                details=details,
            ) from error
        refined_plan_id = _plan_id(refined_plan)
        if refined_plan_id == current_plan_id or refined_plan_id in seen_plan_ids:
            raise WorkflowReplanError(
                "UtterPlan replanning did not produce a new RenderPlan identity: "
                + ("; ".join(summary) or current_plan_id),
                details={
                    **details,
                    "previous_render_plan_id": current_plan_id,
                    "replanned_render_plan_id": refined_plan_id,
                },
            )
        render_plan = refined_plan
        replan_count += 1

    try:
        rendered = voicerender_integration.render_renderplan_memory(
            render_plan,
            preflight=preflight,
            on_event=normalize_workflow_progress("render", "voicerender", on_progress),
        )
    except Exception as error:
        raise WorkflowStageError(
            "render",
            str(error),
            package="voicerender",
            variant=profile_id,
            code=_code(error),
            cause=error,
        ) from error
    if getattr(rendered, "error", None) is not None or not getattr(
        rendered, "selection_complete", False
    ):
        error_message = (
            getattr(rendered, "error", None) or "VoiceRender returned an incomplete selection"
        )
        segment_number = getattr(rendered, "error_segment_number", None)
        segment_id = getattr(rendered, "error_segment_id", None)
        selector = (
            f"speech {segment_number} ({segment_id})"
            if segment_number is not None or segment_id is not None
            else None
        )
        details = {
            "error": error_message,
            "error_code": getattr(rendered, "error_code", None),
            "error_segment_number": segment_number,
            "error_segment_id": segment_id,
            "error_engine": getattr(rendered, "error_engine", None),
            "error_type": getattr(rendered, "error_type", None),
            "error_diagnostic": getattr(rendered, "error_diagnostic", None),
            "fresh_preflight": _preflight_details(rendered.fresh_preflight)
            if getattr(rendered, "fresh_preflight", None) is not None
            else None,
        }
        cause = RuntimeError(str(error_message))
        raise WorkflowStageError(
            "render",
            str(error_message),
            package="voicerender",
            variant=profile_id,
            selector=selector,
            code=getattr(rendered, "error_code", None),
            cause=cause,
            details=details,
        ) from cause

    audio_buffers: dict[tuple[int, str], Any] = {}
    render_warnings = list(getattr(rendered, "warnings", ()))
    for occurrence in rendered.rendered:
        speech = occurrence.speech
        word_timings = tuple(_word_timing(item) for item in getattr(speech, "word_timings", ()))
        audio_buffers[(occurrence.number, occurrence.segment_id)] = (
            audiocompose_integration.rendered_audio_buffer(
                speech.audio,
                speech.sample_rate,
                word_timings=word_timings,
                metadata=getattr(speech, "metadata", {}),
            )
        )
        render_warnings.extend(getattr(speech, "warnings", ()))

    try:
        composed = audiocompose_integration.compose_renderplan_memory(
            render_plan,
            audio_buffers,
            on_progress=normalize_workflow_progress("compose", "audiocompose", on_progress),
        )
    except Exception as error:
        raise WorkflowStageError(
            "compose",
            str(error),
            package="audiocompose",
            variant=profile_id,
            code=_code(error),
            cause=error,
        ) from error

    diagnostics = tuple(getattr(compiled, "diagnostics", ()))
    warnings = tuple(
        dict.fromkeys(
            [
                *(_diagnostic_message(item) for item in diagnostics),
                *render_warnings,
                *getattr(composed, "warnings", ()),
            ]
        )
    )
    audio = np.asarray(composed.audio, dtype=np.float32)
    buffer = AudioBuffer(audio=audio, sample_rate=int(composed.sample_rate))
    return SpeakResult(
        audio=buffer,
        profile_id=profile_id,
        warnings=warnings,
        elapsed_seconds=time.perf_counter() - started,
    )


def _write_speech_wav(result: SpeakResult, output: str | Path) -> Path:
    path = Path(output).expanduser()
    if path.exists() or path.is_symlink():
        raise OutputCollisionError(f"output already exists: {path}")
    audio = np.asarray(result.audio.audio, dtype=np.float32)
    if audio.ndim == 1:
        channels = 1
    elif audio.ndim == 2 and audio.shape[1] > 0:
        channels = int(audio.shape[1])
    else:
        raise ValueError("AudioBuffer has an unsupported audio shape")
    if result.audio.sample_rate <= 0:
        raise ValueError("AudioBuffer has an invalid sample rate")
    pcm = np.rint(np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = path.open("xb")
    except FileExistsError as error:
        raise OutputCollisionError(f"output already exists: {path}") from error
    try:
        with raw, wave.open(raw, "wb") as stream:
            stream.setnchannels(channels)
            stream.setsampwidth(2)
            stream.setframerate(result.audio.sample_rate)
            stream.writeframes(pcm.tobytes())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def speak(
    request: SpeakRequest | str,
    *,
    voice: str | None = None,
    engine: str | None = None,
    target: str | None = None,
    language: str | None = None,
    speaker: str | int | None = None,
    offline: bool = False,
    refresh: bool = False,
    engine_options: Mapping[str, Any] | None = None,
    profile: str | Path | None = None,
    role: str | None = None,
    output: str | Path | None = None,
    no_play: bool = False,
    on_progress: WorkflowProgressHandler = None,
) -> SpeakResult:
    """Speak text semantically, while retaining the transitional function-call form."""
    from readio.api.types import SpeakRequest

    if isinstance(request, str):
        request = SpeakRequest(
            text=request,
            profile=Path(profile) if profile is not None else None,
            role=role,
            voice=voice,
            engine=engine,
            target=target,
            language=language,
            speaker=speaker,
            engine_options=dict(engine_options or {}),
            offline=offline,
            refresh=refresh,
        )
    elif (
        any(
            value is not None
            for value in (voice, engine, target, language, speaker, profile, role, engine_options)
        )
        or offline
        or refresh
    ):
        raise ValueError("pass either a SpeakRequest or direct speak options, not both")

    if request.profile is not None and any(
        value is not None for value in (request.engine, request.target, request.voice)
    ):
        raise ValueError("--profile is mutually exclusive with --voice, --engine, and --target")

    if request.role is not None and request.profile is None:
        raise ValueError("--role requires --profile")

    result = speak_request(request, on_progress=on_progress)
    if output is not None:
        _write_speech_wav(result, output)
    if not no_play:
        play_audio(result.audio.audio, result.audio.sample_rate)
    return result


__all__ = ["MAX_SPEAK_REPLANS", "VoiceSelector", "parse_voice_selector", "speak", "speak_request"]
