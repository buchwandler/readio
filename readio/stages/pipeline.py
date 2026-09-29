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
from ..project_settings import (
    merge_project_synthesis_request,
    project_settings_from_manifest,
    project_synthesis_request,
    project_voice_bindings,
    project_voice_bindings_provenance,
    synthesis_request_fingerprint,
)
from . import audiobook_export as audiobook_export_stage
from .composition import build_audio_job, compose_artifacts, compose_project
from .export import export_project, is_export_current, project_export_states
from .planning import load_scope_plan, plan_project, semantic_status
from .synthesis import synthesize_project

_STAGE_REASON_MESSAGES = {
    "plan.stale.document_format_mismatch": "Plan semantic format does not match the project document.",
    "plan.stale.project_settings_changed": (
        "The semantic plan was built with different project planning settings."
    ),
    "plan.stale.source_changed": "The source changed after planning.",
    "synthesis.missing": "No active synthesis artifacts are available.",
    "synthesis.invalid": "The stored synthesis state is invalid.",
    "synthesis.profile.invalid": "The active synthesis profile is invalid.",
    "synthesis.trace.profile_mismatch": (
        "The synthesis trace does not match the active synthesis profile."
    ),
    "synthesis.stale.plan_changed": "Synthesis is blocked until the plan is rebuilt.",
    "composition.missing": "No composition master has been created.",
    "composition.invalid": "The stored composition state is invalid.",
    "composition.stale.synthesis_changed": "Composition is blocked by stale synthesis.",
    "composition.stale.timing_changed": "Composition timing or presentation changed.",
    "composition.stale.project_settings_changed": (
        "Composition settings differ from those used to build the current master."
    ),
    "synthesis.stale.speech_changed": "Canonical speech artifacts are missing or stale.",
    "synthesis.stale.project_voice_bindings_changed": (
        "Project voice bindings changed after the active synthesis was created."
    ),
    "synthesis.stale.project_settings_changed": (
        "Active synthesis was built with different project synthesis settings."
    ),
    "output.stale.project_settings_changed": (
        "The exported audio does not match the project's desired export settings."
    ),
    "output.missing": "No exported audio file exists for the current composition.",
    "output.invalid": "The stored export state is invalid.",
    "output.stale.composition_changed": "Output is blocked by stale composition.",
    "source.stale.hash_changed": (
        "EPUB source changed after initialization; reinitialize the audiobook project."
    ),
    "document.index.invalid": "The audiobook document index is invalid.",
    "document.chapter.missing": "An indexed chapter Markdown input is missing.",
}


def _project_request(
    project: Project,
    cfg: Any,
    synthesis: SynthesisRequest | None = None,
    *,
    voice_bindings: Mapping[str, str] | None = None,
    use_saved_settings: bool = True,
) -> PlanRequest:
    saved = (
        project_settings_from_manifest(project.manifest, project.root).synthesis
        if use_saved_settings
        else None
    )
    if saved is not None:
        effective_synthesis = merge_project_synthesis_request(saved, synthesis)
    else:
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
    configured_synthesis = project_settings_from_manifest(project.manifest, project.root).synthesis
    recorded_settings = profile.get("project_settings")
    if configured_synthesis is None:
        settings_changed = recorded_settings is not None
    else:
        expected_settings_hash = synthesis_request_fingerprint(
            project_synthesis_request(configured_synthesis), project.root
        )
        settings_changed = (
            not isinstance(recorded_settings, Mapping)
            or recorded_settings.get("synthesis_sha256") != expected_settings_hash
        )
    if settings_changed:
        return {
            "stage": "synthesis",
            "state": "stale",
            "reason": "synthesis.stale.project_settings_changed",
        }
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
    return {
        "code": row["reason"],
        "stage": row["stage"],
        "message": _STAGE_REASON_MESSAGES.get(row["reason"], row["reason"]),
    }


def _audiobook_desired_output_status(
    project: Project, desired: Any, states: tuple[dict[str, Any], ...]
) -> dict[str, Any]:
    target = audiobook_export_stage.audiobook_export_target(project, desired.output)
    prepared = audiobook_export_stage.prepare_audiobook_export(
        project,
        title=desired.title,
        author=desired.author,
        cover=desired.cover,
        bitrate=desired.bitrate,
    )
    if audiobook_export_stage.is_audiobook_export_current(project, target, prepared):
        return {
            "stage": "output",
            "state": "current",
            "reason": "current",
            "format": "m4b",
        }

    for state in states:
        if (
            state.get("format") != "readio.audiobook-export-state"
            or state.get("master_sha256") != prepared.master_sha256
            or state.get("timeline_sha256") != prepared.timeline_sha256
        ):
            continue
        stored_path = Path(str(state.get("path", "")))
        path = stored_path if stored_path.is_absolute() else project.root / stored_path
        if path.is_file() and hash_file(path) == state.get("output_sha256"):
            return {
                "stage": "output",
                "state": "stale",
                "reason": "output.stale.project_settings_changed",
            }

    return {
        "stage": "output",
        "state": "stale",
        "reason": "output.missing",
    }


def project_status(project: Project) -> dict[str, Any]:
    rows = semantic_status(project)
    desired_settings = project_settings_from_manifest(project.manifest, project.root)
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
                desired_composition = desired_settings.composition
                if desired_composition is None:
                    mastering = loudness.get("profile", "spoken-word")
                    target_lufs = loudness.get("target_lufs")
                    true_peak_ceiling_dbtp = loudness.get("true_peak_ceiling_dbtp")
                    peak_policy = loudness.get("peak_policy", "reduce_gain")
                    clip_policy = (
                        identity_payload.get("clip_policy", "clamp")
                        if isinstance(identity_payload, dict)
                        else "clamp"
                    )
                    sample_rate = (
                        identity_payload.get("sample_rate")
                        if isinstance(identity_payload, dict)
                        else None
                    )
                else:
                    mastering = desired_composition.mastering
                    target_lufs = desired_composition.target_lufs
                    true_peak_ceiling_dbtp = desired_composition.true_peak_ceiling_dbtp
                    peak_policy = desired_composition.peak_policy
                    clip_policy = desired_composition.clip_policy
                    sample_rate = desired_composition.sample_rate
                _, current_identity = build_audio_job(
                    project,
                    mastering=mastering,
                    target_lufs=target_lufs,
                    true_peak_ceiling_dbtp=true_peak_ceiling_dbtp,
                    peak_policy=peak_policy,
                    clip_policy=clip_policy,
                    output_sample_rate=sample_rate,
                )
                profile_id = read_json(project.paths["synthesis_profile"]).get("profile_id")
                identity_matches = state.get("composition_id") == current_identity["composition_id"]
                master_matches = hash_file(project.paths["composition_master"]) == state.get(
                    "master_sha256"
                )
                profile_matches = state.get("synthesis_profile_id") == profile_id
                if identity_matches and master_matches and profile_matches:
                    composition = {
                        "stage": "composition",
                        "state": "current",
                        "reason": "current",
                        "composition_id": current_identity["composition_id"],
                    }
                else:
                    reason = (
                        "composition.stale.project_settings_changed"
                        if desired_composition is not None and not identity_matches
                        else "composition.stale.timing_changed"
                    )
                    composition = {
                        "stage": "composition",
                        "state": "stale",
                        "reason": reason,
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
            desired_export = desired_settings.export
            if desired_export is None:
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
                            "format": str(state.get("audio_format")),
                        }
                        break
                else:
                    if states:
                        output = {
                            "stage": "output",
                            "state": "stale",
                            "reason": "output.stale.composition_changed",
                        }
            else:
                audio_format = desired_export.format
                target = desired_export.output or (
                    project.root / "output" / f"{project.manifest.name}.{audio_format}"
                )
                if not target.is_absolute():
                    target = project.root / target
                if is_export_current(
                    project,
                    target,
                    audio_format=audio_format,
                    bitrate=desired_export.bitrate,
                ):
                    output = {
                        "stage": "output",
                        "state": "current",
                        "reason": "current",
                        "format": audio_format,
                    }
                else:
                    has_valid_current_export = False
                    has_current_master_state = False
                    for state in states:
                        if state.get("master_sha256") != current_master_sha:
                            continue
                        has_current_master_state = True
                        stored_path = Path(str(state.get("path", "")))
                        path = (
                            stored_path if stored_path.is_absolute() else project.root / stored_path
                        )
                        if path.is_file() and hash_file(path) == state.get("output_sha256"):
                            has_valid_current_export = True
                            break
                    if has_valid_current_export:
                        output = {
                            "stage": "output",
                            "state": "stale",
                            "reason": "output.stale.project_settings_changed",
                        }
                    elif has_current_master_state:
                        output = {
                            "stage": "output",
                            "state": "stale",
                            "reason": "output.invalid",
                        }
                    elif states:
                        output = {
                            "stage": "output",
                            "state": "stale",
                            "reason": "output.stale.composition_changed",
                        }
        except (OSError, KeyError, TypeError, ValueError):
            output = {"stage": "output", "state": "stale", "reason": "output.invalid"}
    if (
        composition["state"] == "current"
        and project.manifest.kind == "audiobook"
        and desired_settings.audiobook_export is not None
    ):
        try:
            output = _audiobook_desired_output_status(
                project,
                desired_settings.audiobook_export,
                project_export_states(project),
            )
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
    request: ProjectBuildRequest | None,
    *,
    on_synthesis_event: Callable[[Any], None] | None = None,
    on_composition_progress: CompositionProgressCallback | None = None,
    on_phase: Callable[[str], None] | None = None,
    on_stage: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    if request is None:
        saved = project_settings_from_manifest(project.manifest, project.root)
        request = ProjectBuildRequest(
            composition=saved.composition or CompositionOptions(),
            export=saved.export or ExportOptions(),
        )
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
        mastering=composition_options.mastering,
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
        operations.append(
            {
                "stage": "composition",
                "action": "skipped",
                "mastering_profile": identity["identity_payload"]["loudness"]["profile"],
                "loudness": state.get("loudness"),
            }
        )
        report("composition", "skipped")
    else:
        report("composition", "started")
        composed = compose_project(
            project,
            mastering=composition_options.mastering,
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
        mastering=options.mastering,
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
