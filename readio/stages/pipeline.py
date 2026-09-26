"""Status and high-level orchestration for project builds."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from audiocompose import CompositionProgressCallback

from ..api.types import CompositionOptions, ExportOptions, ProjectBuildRequest
from ..formats import AudioFormat
from ..plan import InputRequest, OutputRequest, PlanRequest, SynthesisRequest
from ..project import Project, hash_file, read_json
from ..project_settings import project_voice_bindings, project_voice_bindings_provenance
from .composition import build_audio_job, compose_artifacts, compose_project
from .export import export_project, is_export_current, project_export_states
from .planning import load_scope_plan, plan_project, semantic_status
from .synthesis import synthesize_project


def _project_request(
    project: Project,
    cfg: Any,
    synthesis: SynthesisRequest | None = None,
    *,
    voice_bindings: Mapping[str, str] | None = None,
) -> PlanRequest:
    effective_synthesis = synthesis or SynthesisRequest(language=cfg.reader.lang)
    return PlanRequest(
        operation="render",
        input=InputRequest(
            document=project.load_document_scope(project.document_scopes()[0]),
            selector="all",
            source_kind="file",
        ),
        synthesis=effective_synthesis,
        output=OutputRequest(mode="file", requested_format="wav", force=True),
        voice_bindings=voice_bindings or {},
    )


def _synthesis_status(project: Project) -> dict[str, Any]:
    trace_path = project.paths["synthesis_trace"]
    profile_path = project.paths["synthesis_profile"]
    if not profile_path.is_file():
        return {"stage": "synthesis", "state": "stale", "reason": "synthesis.missing"}
    try:
        plan_scopes = project.load_plan_index().scopes
        scoped_plans = tuple((scope, load_scope_plan(project, scope)) for scope in plan_scopes)
        profile = read_json(profile_path)
    except (OSError, KeyError, TypeError, ValueError):
        return {"stage": "synthesis", "state": "stale", "reason": "synthesis.invalid"}

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
    binding_record = profile.get("project_voice_bindings")
    if binding_record is None:
        legacy_provider = {"pykokoro": "kokoro", "piper": "piper"}.get(
            str(canonical.get("engine", ""))
        )
        legacy_bindings = (
            project_voice_bindings(project.manifest, legacy_provider)
            if legacy_provider is not None
            else {}
        )
        if legacy_bindings:
            current_bindings = project_voice_bindings_provenance(legacy_provider, legacy_bindings)
            return {
                "stage": "synthesis",
                "state": "stale",
                "reason": "synthesis.stale.project_voice_bindings_changed",
                "project_voice_bindings": current_bindings,
            }
    else:
        if not isinstance(binding_record, Mapping):
            return {"stage": "synthesis", "state": "stale", "reason": "synthesis.profile.invalid"}
        provider = binding_record.get("provider")
        stored_bindings = binding_record.get("bindings")
        stored_hash = binding_record.get("sha256")
        if (
            not isinstance(provider, str)
            or not isinstance(stored_bindings, Mapping)
            or not isinstance(stored_hash, str)
        ):
            return {"stage": "synthesis", "state": "stale", "reason": "synthesis.profile.invalid"}
        recorded_bindings = project_voice_bindings_provenance(provider, stored_bindings)
        if recorded_bindings["sha256"] != stored_hash:
            return {"stage": "synthesis", "state": "stale", "reason": "synthesis.profile.invalid"}
        current_bindings = project_voice_bindings_provenance(
            provider, project_voice_bindings(project.manifest, provider)
        )
        if current_bindings["sha256"] != stored_hash:
            return {
                "stage": "synthesis",
                "state": "stale",
                "reason": "synthesis.stale.project_voice_bindings_changed",
                "project_voice_bindings": current_bindings,
            }

    current_plans = {scope.id: (scope, plan) for scope, plan in scoped_plans}
    trace_plans = trace.get("plans")
    if isinstance(trace_plans, list):
        for recorded in trace_plans:
            if not isinstance(recorded, dict):
                continue
            current = current_plans.get(str(recorded.get("scope_id", "")))
            if current is None:
                return {
                    "stage": "synthesis",
                    "state": "stale",
                    "reason": "synthesis.stale.plan_changed",
                }
            scope, plan = current
            current_sha = hash_file(project.root / "plan" / scope.path)
            if (
                recorded.get("plan_id") != plan.plan_id
                or recorded.get("plan_sha256") != current_sha
            ):
                return {
                    "stage": "synthesis",
                    "state": "stale",
                    "reason": "synthesis.stale.plan_changed",
                    "scope_id": scope.id,
                }

    from .speech_identity import segment_speech_hash, segment_synthesis_key
    from .synthesis import _valid_audio

    per_scope: dict[str, dict[str, int]] = {}
    for scope, plan in scoped_plans:
        reusable = 0
        for segment in plan.segments:
            speech_hash = segment_speech_hash(plan, segment, canonical)
            key = segment_synthesis_key(speech_hash, profile_id)
            cache_path = project.root / "synthesis" / "cache" / f"{key.replace(':', '-')}.wav"
            sidecar_path = project.root / "synthesis" / "cache" / f"{key.replace(':', '-')}.json"
            checked = _valid_audio(cache_path)
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
        per_scope[scope.id] = {"reusable": reusable, "required": len(plan.segments)}

    reusable = sum(item["reusable"] for item in per_scope.values())
    required = sum(item["required"] for item in per_scope.values())
    details = {
        "reusable": reusable,
        "required": required,
        "missing": required - reusable,
        "total": required,
        "scopes": len(scoped_plans),
        "per_scope": per_scope,
        "profile": profile,
        "profile_id": profile_id,
        "plan_ids": [
            {"scope_id": scope.id, "plan_id": plan.plan_id} for scope, plan in scoped_plans
        ],
    }
    if len(scoped_plans) == 1:
        details["plan_id"] = scoped_plans[0][1].plan_id
    if reusable == required:
        return {"stage": "synthesis", "state": "current", "reason": "current", **details}
    return {
        "stage": "synthesis",
        "state": "stale",
        "reason": "synthesis.stale.speech_changed",
        **details,
    }


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
        "synthesis.stale.project_voice_bindings_changed": "Project voice bindings changed after the active synthesis was created.",
        "output.stale.composition_changed": "Output is blocked by stale composition.",
        "source.stale.hash_changed": "EPUB source changed after initialization; reinitialize the audiobook project.",
        "document.index.invalid": "The audiobook document index is invalid.",
        "document.chapter.missing": "An indexed chapter Markdown input is missing.",
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
                loudness = (
                    identity_payload.get("loudness", {})
                    if isinstance(identity_payload, dict)
                    else {}
                )
                _, current_identity = build_audio_job(
                    project,
                    target_lufs=loudness.get("target_lufs"),
                    true_peak_ceiling_dbtp=loudness.get("true_peak_ceiling_dbtp", -1.0),
                    peak_policy=loudness.get("peak_policy", "reduce_gain"),
                    clip_policy=identity_payload.get("clip_policy", "clamp")
                    if isinstance(identity_payload, dict)
                    else "clamp",
                    output_sample_rate=identity_payload.get("sample_rate")
                    if isinstance(identity_payload, dict)
                    else None,
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
        try:
            states = project_export_states(project)
            current_master_sha = hash_file(project.paths["composition_master"])
            for state in states:
                stored_path = Path(str(state.get("path", "")))
                path = stored_path if stored_path.is_absolute() else project.root / stored_path
                if (
                    path.is_file()
                    and hash_file(path) == state.get("output_sha256")
                    and state.get("master_sha256") == current_master_sha
                ):
                    output = {
                        "stage": "output",
                        "state": "current",
                        "reason": "current",
                        "format": state.get("audio_format"),
                    }
                    break
            else:
                if states:
                    output = {
                        "stage": "output",
                        "state": "stale",
                        "reason": "output.stale.composition_changed",
                    }
        except (OSError, KeyError, TypeError, ValueError):
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


def build_project(
    project: Project,
    cfg: Any,
    request: ProjectBuildRequest,
    *,
    on_synthesis_event: Callable[[Any], None] | None = None,
    on_composition_progress: CompositionProgressCallback | None = None,
    on_phase: Callable[[str], None] | None = None,
    on_stage: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    if request.target not in {"plan", "synthesis", "composition", "export"}:
        raise ValueError(f"unknown project build target: {request.target}")
    operations: list[dict[str, Any]] = []

    def report(stage: str, state: str) -> None:
        if on_stage is not None:
            on_stage(stage, state)

    status = project_status(project)
    plan_state = next(row["state"] for row in status["stages"] if row["stage"] == "plan")
    if plan_state == "current":
        operations.append({"stage": "plan", "action": "skipped"})
        report("plan", "skipped")
    else:
        report("plan", "started")
        planned = plan_project(project, cfg)
        operations.append({"stage": "plan", "action": "rebuilt", "scopes": len(planned.scopes)})
        report("plan", "rebuilt")
    if request.target == "plan":
        return {"project": str(project.root), "operations": operations, "output_path": None}

    report("synthesis", "started")
    synthesis = synthesize_project(
        project,
        cfg,
        request=_project_request(
            project,
            cfg,
            request.synthesis,
            voice_bindings=request.voice_bindings,
        ),
        selector=request.selection if request.target == "synthesis" else "all",
        activate=True,
        on_event=on_synthesis_event,
    )
    synthesis_action = "rebuilt" if synthesis["rendered"] else "skipped"
    operations.append(
        {
            "stage": "synthesis",
            "action": synthesis_action,
            "reused": synthesis["reused"],
            "rendered": synthesis["rendered"],
            "profile_id": synthesis["profile"].profile_id,
        }
    )
    report("synthesis", synthesis_action)
    if request.target == "synthesis":
        return {"project": str(project.root), "operations": operations, "output_path": None}

    composition_options = request.composition
    _, identity = build_audio_job(
        project,
        target_lufs=composition_options.target_lufs,
        true_peak_ceiling_dbtp=composition_options.true_peak_ceiling_dbtp,
        peak_policy=composition_options.peak_policy,
        clip_policy=composition_options.clip_policy,
        output_sample_rate=composition_options.sample_rate,
    )
    state_path = project.paths["composition_state"]
    state = read_json(state_path) if state_path.is_file() else {}
    composition_current = (
        state.get("composition_id") == identity["composition_id"]
        and project.paths["composition_master"].is_file()
    )
    if composition_current:
        operations.append({"stage": "composition", "action": "skipped"})
        report("composition", "skipped")
    else:
        report("composition", "started")
        composed = compose_project(
            project,
            target_lufs=composition_options.target_lufs,
            true_peak_ceiling_dbtp=composition_options.true_peak_ceiling_dbtp,
            peak_policy=composition_options.peak_policy,
            clip_policy=composition_options.clip_policy,
            output_sample_rate=composition_options.sample_rate,
            on_progress=on_composition_progress,
            on_phase=on_phase,
        )
        operations.append({"stage": "composition", "action": "rebuilt", **composed})
        report("composition", "rebuilt")
    if request.target == "composition":
        return {"project": str(project.root), "operations": operations, "output_path": None}

    export_options = request.export
    audio_format = export_options.format
    target = export_options.output or (
        project.root / "output" / f"{project.manifest.name}.{audio_format}"
    )
    if not target.is_absolute():
        target = project.root / target
    current_output = is_export_current(
        project,
        target,
        audio_format=audio_format,
        bitrate=export_options.bitrate,
    )
    if current_output:
        operations.append({"stage": "export", "action": "skipped", "path": target})
        report("export", "skipped")
    else:
        report("export", "started")
        exported = export_project(
            project,
            audio_format=audio_format,
            bitrate=export_options.bitrate,
            output=target,
            force=export_options.force,
        )
        operations.append({"stage": "export", "action": "rebuilt", **exported})
        report("export", "rebuilt")
    return {"project": str(project.root), "operations": operations, "output_path": target}


def render_project(
    project: Project,
    cfg: Any,
    *,
    audio_format: str = "wav",
    target_lufs: float | None = None,
    synthesis: SynthesisRequest | None = None,
    selector: str = "all",
    on_synthesis_event: Callable[[Any], None] | None = None,
    on_composition_progress: CompositionProgressCallback | None = None,
    on_phase: Callable[[str], None] | None = None,
    on_stage: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    request = ProjectBuildRequest(
        target="export",
        selection=selector,
        synthesis=synthesis or SynthesisRequest(language=cfg.reader.lang),
        composition=CompositionOptions(target_lufs=target_lufs),
        export=ExportOptions(format=cast(AudioFormat, audio_format)),
    )
    return build_project(
        project,
        cfg,
        request,
        on_synthesis_event=on_synthesis_event,
        on_composition_progress=on_composition_progress,
        on_phase=on_phase,
        on_stage=on_stage,
    )


def preview_project(
    project: Project,
    cfg: Any,
    *,
    request: PlanRequest,
    selector: str,
    output: Path | None = None,
    target_lufs: float | None = None,
    composition: CompositionOptions | None = None,
    activate: bool = False,
    on_event: Any = None,
    on_composition_progress: CompositionProgressCallback | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    synthesis = synthesize_project(
        project,
        cfg,
        request=request,
        selector=selector,
        activate=activate,
        on_event=on_event,
    )
    selected_scope_ids = {artifact.scope_id for artifact in synthesis["artifacts"]}
    scoped_plans = tuple(
        (scope.id, load_scope_plan(project, scope))
        for scope in project.load_plan_index().scopes
        if scope.id in selected_scope_ids
    )
    document_scopes = {scope.id: scope for scope in project.document_scopes()}
    indexed_plans = {scope.id: scope for scope in project.load_plan_index().scopes}
    scope_metadata = tuple(
        {
            "scope_id": scope_id,
            "kind": document_scopes[scope_id].kind,
            "title": document_scopes[scope_id].title,
            "source_number": document_scopes[scope_id].source_number,
            "plan_id": plan.plan_id,
            "plan_sha256": hash_file(project.root / "plan" / indexed_plans[scope_id].path),
        }
        for scope_id, plan in scoped_plans
    )
    options = composition or CompositionOptions(target_lufs=target_lufs)
    result = compose_artifacts(
        synthesis["artifacts"],
        plans=scoped_plans,
        scope_metadata=scope_metadata,
        target_lufs=options.target_lufs,
        true_peak_ceiling_dbtp=options.true_peak_ceiling_dbtp,
        peak_policy=options.peak_policy,
        clip_policy=options.clip_policy,
        output_sample_rate=options.sample_rate,
        composition=synthesis["profile"].payload.get("composition", {}),
        synthesis_profile=synthesis["profile"].payload,
        output=output,
        on_progress=on_composition_progress,
        on_phase=on_phase,
    )
    return {
        "profile_id": synthesis["profile"].profile_id,
        "plan_ids": synthesis["plan_ids"],
        "reused": synthesis["reused"],
        "rendered": synthesis["rendered"],
        "activated": activate,
        **result,
    }


__all__ = ["build_project", "preview_project", "project_status", "render_project"]
