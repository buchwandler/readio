"""Status and high-level orchestration for project builds."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..plan import InputRequest, OutputRequest, PlanRequest, SynthesisRequest
from ..project import Project, hash_file, read_json
from .composition import build_audio_job, compose_artifacts, compose_project
from .export import export_project
from .planning import load_scope_plan, plan_project, semantic_status
from .synthesis import synthesize_project


def _project_request(project: Project, cfg: Any, args: Any = None) -> PlanRequest:
    reader = cfg.reader
    engine = getattr(args, "engine", None) or reader.engine if args is not None else reader.engine
    voice = getattr(args, "voice", None) or reader.voice if args is not None else reader.voice
    return PlanRequest(
        operation="render",
        input=InputRequest(document=project.document(), selector="all", source_kind="file"),
        synthesis=SynthesisRequest(
            language=getattr(args, "lang", None) if args is not None else reader.lang,
            engine=engine,
            voice=voice,
            model=getattr(args, "model", None) if args is not None else None,
            speed=getattr(args, "speed", None) if args is not None else None,
            pause_mode=getattr(args, "pause_mode", None) if args is not None else None,
            unit=getattr(args, "unit", None) if args is not None else None,
        ),
        output=OutputRequest(mode="file", requested_format="wav", force=True),
    )


def _synthesis_status(project: Project) -> dict[str, Any]:
    trace_path = project.paths["synthesis_trace"]
    profile_path = project.paths["synthesis_profile"]
    if not profile_path.is_file():
        return {"stage": "synthesis", "state": "stale", "reason": "synthesis.missing"}
    try:
        plan = load_scope_plan(project)
        profile = read_json(profile_path)
        trace: dict[str, Any] = {}
        if trace_path.is_file():
            try:
                candidate = read_json(trace_path)
            except (OSError, ValueError):
                candidate = {}
            if isinstance(candidate, dict) and candidate.get("format") == "readio.synthesis-trace":
                trace = candidate
        if profile.get("format") != "readio.synthesis-profile":
            return {"stage": "synthesis", "state": "stale", "reason": "synthesis.profile.invalid"}
        profile_id = str(profile.get("profile_id", ""))
        trace_profile = trace.get("profile")
        if trace_profile is not None and (
            not isinstance(trace_profile, dict) or trace_profile.get("profile_id") != profile_id
        ):
            return {
                "stage": "synthesis",
                "state": "stale",
                "reason": "synthesis.trace.profile_mismatch",
            }
        canonical = profile.get("canonical")
        if not isinstance(canonical, dict):
            return {"stage": "synthesis", "state": "stale", "reason": "synthesis.profile.invalid"}
        from .speech_identity import segment_speech_hash, segment_synthesis_key
        from .synthesis import _valid_audio
        reusable = 0
        for segment in plan.segments:
            speech_hash = segment_speech_hash(plan, segment, canonical)
            key = segment_synthesis_key(speech_hash, profile_id)
            cache_dir = project.root / "synthesis" / "cache"
            audio_path = cache_dir / f"{key.replace(':', '-')}.wav"
            sidecar_path = cache_dir / f"{key.replace(':', '-')}.json"
            checked = _valid_audio(audio_path)
            if checked is None:
                continue
            try:
                sidecar = read_json(sidecar_path)
            except (OSError, ValueError):
                continue
            if (
                sidecar.get("speech_hash") == speech_hash
                and sidecar.get("synthesis_key") == key
                and sidecar.get("profile_id") == profile_id
                and sidecar.get("audio_sha256") == checked[3]
            ):
                reusable += 1
        details = {
            "reusable": reusable,
            "required": len(plan.segments),
            "missing": len(plan.segments) - reusable,
            "total": len(plan.segments),
            "profile": profile,
            "profile_id": profile_id,
            "plan_id": plan.plan_id,
        }
        if reusable == len(plan.segments):
            return {"stage": "synthesis", "state": "current", "reason": "current", **details}
        return {
            "stage": "synthesis",
            "state": "stale",
            "reason": "synthesis.stale.speech_changed",
            **details,
        }
    except (OSError, KeyError, TypeError, ValueError):
        return {"stage": "synthesis", "state": "stale", "reason": "synthesis.invalid"}

def _stage_issue(row: dict[str, Any]) -> dict[str, Any] | None:
    if row["state"] == "current":
        return None
    messages = {
        "plan.stale.document_format_mismatch": "Plan semantic format does not match the project document.",
        "plan.stale.source_changed": "The source changed after planning.",
        "synthesis.missing": "No active synthesis artifacts are available.",
        "synthesis.stale.plan_changed": "Synthesis is blocked until the plan is rebuilt.",
        "composition.stale.synthesis_changed": "Composition is blocked by stale synthesis.",
        "composition.stale.timing_changed": "Composition timing or presentation changed.",
        "synthesis.stale.speech_changed": "Canonical speech artifacts are missing or stale.",
        "output.stale.composition_changed": "Output is blocked by stale composition.",
    }
    return {
        "code": row["reason"],
        "stage": row["stage"],
        "message": messages.get(row["reason"], row["reason"]),
    }


def project_status(project: Project) -> dict[str, Any]:
    rows = semantic_status(project)
    plan_current = rows[-1]["state"] == "current"
    synthesis = (
        _synthesis_status(project)
        if plan_current
        else {
            "stage": "synthesis",
            "state": "stale",
            "reason": "synthesis.stale.plan_changed",
            "blocked_by": "plan",
        }
    )
    if synthesis["state"] != "current":
        composition = {
            "stage": "composition",
            "state": "stale",
            "reason": "composition.stale.synthesis_changed",
            "blocked_by": "synthesis",
        }
    else:
        composition = {"stage": "composition", "state": "stale", "reason": "composition.missing"}
        state_path = project.paths["composition_state"]
        if state_path.is_file() and project.paths["composition_master"].is_file():
            try:
                state = read_json(state_path)
                identity_payload = state.get("identity_payload", {})
                loudness = identity_payload.get("loudness", {}) if isinstance(identity_payload, dict) else {}
                _, current_identity = build_audio_job(
                    project,
                    target_lufs=loudness.get("target_lufs"),
                    true_peak_ceiling_dbtp=loudness.get("true_peak_ceiling_dbtp", -1.0),
                    peak_policy=loudness.get("peak_policy", "reduce_gain"),
                    clip_policy=identity_payload.get("clip_policy", "clamp") if isinstance(identity_payload, dict) else "clamp",
                )
                profile_id = read_json(project.paths["synthesis_profile"]).get("profile_id")
                if (
                    state.get("composition_id") == current_identity["composition_id"]
                    and hash_file(project.paths["composition_master"]) == state.get("master_sha256")
                    and state.get("synthesis_profile_id") == profile_id
                ):
                    composition = {
                        "stage": "composition",
                        "state": "current",
                        "reason": "current",
                        "composition_id": state.get("composition_id"),
                    }
                else:
                    composition = {
                        "stage": "composition",
                        "state": "stale",
                        "reason": "composition.stale.timing_changed",
                    }
            except (OSError, ValueError, KeyError):
                composition = {
                    "stage": "composition",
                    "state": "stale",
                    "reason": "composition.invalid",
                }
    if composition["state"] != "current":
        output = {
            "stage": "output",
            "state": "stale",
            "reason": "output.stale.composition_changed",
            "blocked_by": "composition",
        }
    else:
        output = {"stage": "output", "state": "stale", "reason": "output.missing"}
        output_state = project.root / "output" / "state.json"
        if output_state.is_file():
            try:
                state = read_json(output_state)
                path = project.root / str(state["path"])
                if (
                    path.is_file()
                    and hash_file(path) == state.get("output_sha256")
                    and state.get("master_sha256") == hash_file(project.paths["composition_master"])
                ):
                    output = {
                        "stage": "output",
                        "state": "current",
                        "reason": "current",
                        "format": state.get("audio_format"),
                    }
                else:
                    output = {
                        "stage": "output",
                        "state": "stale",
                        "reason": "output.stale.composition_changed",
                    }
            except (OSError, KeyError, ValueError):
                output = {"stage": "output", "state": "stale", "reason": "output.invalid"}
    stages = [*rows, synthesis, composition, output]
    issues = [issue for row in stages if (issue := _stage_issue(row)) is not None]
    commands = {
        "plan": "readio plan",
        "synthesis": "readio synth",
        "composition": "readio compose",
        "output": "readio export --format mp3",
    }
    next_actions = []
    for row in stages:
        if row["stage"] in {"source", "document"} or row["state"] == "current":
            continue
        next_actions.append(
            {
                "stage": row["stage"],
                "command": commands.get(row["stage"], "readio status"),
                "reason": row["reason"],
            }
        )
    return {
        "project": str(project.root),
        "name": project.manifest.name,
        "source": {"path": project.manifest.source_path, "format": project.manifest.source_format},
        "stages": stages,
        "issues": issues,
        "next_actions": next_actions[:1],
    }


def render_project(
    project: Project,
    cfg: Any,
    *,
    audio_format: str = "wav",
    args: Any = None,
    target_lufs: float | None = None,
) -> dict[str, Any]:
    operations: list[dict[str, Any]] = []
    status = project_status(project)
    if any(row["stage"] == "plan" and row["state"] != "current" for row in status["stages"]):
        plan_project(project, cfg)
        operations.append({"stage": "plan", "action": "rebuilt"})
    else:
        operations.append({"stage": "plan", "action": "skipped"})
    request = _project_request(project, cfg, args)
    synthesis = synthesize_project(project, cfg, request=request, activate=True)
    operations.append(
        {
            "stage": "synthesis",
            "action": "rebuilt" if synthesis["rendered"] else "skipped",
            "reused": synthesis["reused"],
            "rendered": synthesis["rendered"],
        }
    )
    _, identity = build_audio_job(project, target_lufs=target_lufs)
    state = (
        read_json(project.paths["composition_state"])
        if project.paths["composition_state"].is_file()
        else {}
    )
    if (
        state.get("composition_id") == identity["composition_id"]
        and project.paths["composition_master"].is_file()
    ):
        operations.append({"stage": "composition", "action": "skipped"})
    else:
        composed = compose_project(project, target_lufs=target_lufs)
        operations.append({"stage": "composition", "action": "rebuilt", **composed})
    output_state = project.root / "output" / "state.json"
    output_path = project.root / "output" / f"{project.manifest.name}.{audio_format}"
    expected = {
        "audio_format": audio_format,
        "master_sha256": hash_file(project.paths["composition_master"]),
    }
    old = read_json(output_state) if output_state.is_file() else {}
    if (
        old.get("audio_format") == expected["audio_format"]
        and old.get("master_sha256") == expected["master_sha256"]
        and output_path.is_file()
        and hash_file(output_path) == old.get("output_sha256")
    ):
        operations.append({"stage": "export", "action": "skipped", "path": output_path})
    else:
        exported = export_project(project, audio_format=audio_format, output=output_path)
        operations.append({"stage": "export", "action": "rebuilt", **exported})
    return {"project": str(project.root), "operations": operations}


def preview_project(
    project: Project,
    cfg: Any,
    *,
    request: PlanRequest,
    selector: str,
    output: Path | None = None,
    target_lufs: float | None = None,
    activate: bool = False,
    on_event: Any = None,
) -> dict[str, Any]:
    synthesis = synthesize_project(
        project,
        cfg,
        request=request,
        selector=selector,
        activate=activate,
        on_event=on_event,
    )
    result = compose_artifacts(
        synthesis["artifacts"],
        plan=load_scope_plan(project),
        target_lufs=target_lufs,
        composition=synthesis["profile"].payload.get("composition", {}),
        output=output,
    )
    return {
        "profile_id": synthesis["profile"].profile_id,
        "plan_id": synthesis.get("plan_id"),
        "reused": synthesis["reused"],
        "rendered": synthesis["rendered"],
        "activated": activate,
        **result,
    }


__all__ = ["preview_project", "project_status", "render_project"]
