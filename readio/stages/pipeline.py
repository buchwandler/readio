"""Status and high-level orchestration for project builds."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from voicerender import ArtifactError, read_render_manifest

from ..api.types import CompositionOptions, ExportOptions, ProjectBuildRequest
from ..audioio import write_pcm16_wav
from ..errors import ProjectPlanAttemptError
from ..formats import AudioFormat
from ..integrations.audioexport import AudioExportIntegrationError, AudioExportUnavailableError
from ..plan import InputRequest, OutputRequest, PlanRequest, SynthesisRequest
from ..project import Project, canonical_json, hash_file, read_json
from ..project_settings import (
    merge_export_options,
    merge_project_synthesis_request,
    project_role_targets_provenance,
    project_settings_from_manifest,
    project_synthesis_request,
    synthesis_request_fingerprint,
)
from . import audiobook_export as audiobook_export_stage
from .composition import (
    CompositionProgressCallback,
    _loudness_payload,
    build_audio_job,
    compose_job,
    compose_project,
    composition_is_current,
)
from .export import (
    export_project,
    is_export_current,
    output_state_for,
    project_export_states,
    resolve_project_export_target,
)
from .planning import (
    PlanningProgressCallback,
    inspect_plan_state,
    load_scope_plan,
    plan_project,
    semantic_status,
)
from .synthesis import synthesize_project

_STAGE_REASON_MESSAGES = {
    "plan.stale.document_format_mismatch": "Plan semantic format does not match the project document.",
    "plan.invalid.not_renderable": (
        "Stored semantic plan is invalid under the current renderer contract. "
        "Rebuild with: readio plan build ."
    ),
    "plan.stale.planner_version_changed": (
        "The semantic planner contract changed. Rebuild with: readio plan build ."
    ),
    "plan.artifact.legacy_format": (
        "Stored plan uses legacy JSON and must be regenerated from source. "
        "Rebuild with: readio plan build ."
    ),
    "plan.artifact.schema_mismatch": (
        "Stored plan schema is unsupported. Rebuild with: readio plan build ."
    ),
    "plan.artifact.invalid": "Stored semantic plan artifact is invalid. Rebuild with: readio plan build .",
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
    "synthesis.stale.project_role_targets_changed": (
        "Project role targets changed after the active synthesis was created."
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
        "The original source changed after project initialization. Persisted semantic SSMD remains "
        "authoritative; reinitialize or import again to use the changed source."
    ),
    "document.index.invalid": "The audiobook document index is invalid.",
    "document.chapter.missing": "An indexed chapter SSMD input is missing.",
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
        project_settings_from_manifest(project.manifest, project.state_root).synthesis
        if use_saved_settings
        else None
    )
    if saved is not None:
        effective_synthesis = merge_project_synthesis_request(saved, synthesis)
    else:
        effective_synthesis = synthesis or SynthesisRequest()
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
    configured_synthesis = project_settings_from_manifest(
        project.manifest, project.state_root
    ).synthesis
    recorded_settings = profile.get("project_settings")
    if configured_synthesis is None:
        settings_changed = recorded_settings is not None
    else:
        expected_settings_hash = synthesis_request_fingerprint(
            project_synthesis_request(configured_synthesis), project.state_root
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
    target_record = profile.get("project_role_targets")
    if not isinstance(target_record, Mapping):
        return {"stage": "synthesis", "state": "stale", "reason": "synthesis.profile.invalid"}
    stored_targets = target_record.get("bindings")
    stored_ambiguities = target_record.get("ambiguities")
    stored_target_hash = target_record.get("sha256")
    if (
        not isinstance(stored_targets, Mapping)
        or not isinstance(stored_ambiguities, Mapping)
        or not isinstance(stored_target_hash, str)
    ):
        return {"stage": "synthesis", "state": "stale", "reason": "synthesis.profile.invalid"}
    stored_value = {"bindings": stored_targets, "ambiguities": stored_ambiguities}
    if hashlib.sha256(canonical_json(stored_value)).hexdigest() != stored_target_hash:
        return {"stage": "synthesis", "state": "stale", "reason": "synthesis.profile.invalid"}
    current_targets = project_role_targets_provenance(project.manifest)
    if current_targets["sha256"] != stored_target_hash:
        return {
            "stage": "synthesis",
            "state": "stale",
            "reason": "synthesis.stale.project_role_targets_changed",
            "project_role_targets": current_targets,
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
            current_sha = hash_file(project.state_root / "plan" / scope.path)
            if (
                recorded.get("plan_id") != plan.plan_id
                or recorded.get("sha256", recorded.get("plan_sha256")) != current_sha
            ):
                return {
                    "stage": "synthesis",
                    "state": "stale",
                    "reason": "synthesis.stale.plan_changed",
                    "scope_id": scope.id,
                }

    if profile.get("schema_version") == 6:
        manifest_refs = profile.get("manifest_refs")
        if not isinstance(manifest_refs, list) or trace.get("manifests") != manifest_refs:
            return {"stage": "synthesis", "state": "stale", "reason": "synthesis.trace.manifest_mismatch"}
        current_plans = {scope.id: (scope, plan) for scope, plan in scoped_plans}
        per_scope: dict[str, dict[str, int]] = {}
        required = 0
        reusable = 0
        for reference in manifest_refs:
            if not isinstance(reference, Mapping):
                return {"stage": "synthesis", "state": "stale", "reason": "synthesis.profile.invalid"}
            scope_id = str(reference.get("scope_id", ""))
            current = current_plans.get(scope_id)
            if current is None:
                return {"stage": "synthesis", "state": "stale", "reason": "synthesis.stale.plan_changed"}
            scope, plan = current
            manifest_value = reference.get("path")
            if not isinstance(manifest_value, str):
                return {"stage": "synthesis", "state": "stale", "reason": "synthesis.profile.invalid"}
            manifest_path = Path(manifest_value)
            if not manifest_path.is_absolute():
                manifest_path = project.state_root / manifest_path
            try:
                manifest = read_render_manifest(manifest_path)
            except (ArtifactError, OSError, ValueError):
                return {"stage": "synthesis", "state": "stale", "reason": "synthesis.stale.render_manifest_missing"}
            plan_path = project.state_root / "plan" / scope.path
            source = manifest.get("source", {})
            manifest_profile = manifest.get("profile", {})
            current_plan_sha = f"sha256:{hash_file(plan_path)}"
            if (
                manifest.get("manifest_id") != reference.get("manifest_id")
                or source.get("plan_id") != plan.plan_id
                or source.get("plan_file_sha256") != current_plan_sha
                or source.get("plan_id") != reference.get("plan_id")
                or source.get("plan_file_sha256") != reference.get("plan_file_sha256")
                or manifest_profile.get("effective_profile_sha256")
                != reference.get("effective_profile_sha256")
            ):
                return {"stage": "synthesis", "state": "stale", "reason": "synthesis.stale.render_manifest_changed"}
            entries = manifest.get("segments")
            if not isinstance(entries, list):
                return {"stage": "synthesis", "state": "stale", "reason": "synthesis.profile.invalid"}
            scope_counts = per_scope.setdefault(scope_id, {"reusable": 0, "required": 0})
            for entry in entries:
                required += 1
                scope_counts["required"] += 1
                if not isinstance(entry, Mapping) or entry.get("status") not in {"rendered", "reused"}:
                    continue
                audio = entry.get("audio", {})
                if not isinstance(audio, Mapping) or not isinstance(audio.get("path"), str):
                    continue
                audio_path = Path(audio["path"])
                if not audio_path.is_absolute():
                    audio_path = manifest_path.parent / audio_path
                if not audio_path.is_file() or hash_file(audio_path) != audio.get("sha256"):
                    continue
                reusable += 1
                scope_counts["reusable"] += 1
        if required != trace.get("selected_segments", required):
            return {"stage": "synthesis", "state": "stale", "reason": "synthesis.stale.render_manifest_changed"}
        details = {
            "reusable": reusable,
            "required": required,
            "missing": required - reusable,
            "total": required,
            "scopes": len(scoped_plans),
            "per_scope": per_scope,
            "profile": profile,
            "profile_id": profile_id,
            "manifests": manifest_refs,
            "plan_ids": [
                {"scope_id": scope.id, "plan_id": plan.plan_id}
                for scope, plan in scoped_plans
            ],
        }
        if len(scoped_plans) == 1:
            details["plan_id"] = scoped_plans[0][1].plan_id
        if reusable == required:
            return {"stage": "synthesis", "state": "current", "reason": "current", **details}
        return {
            "stage": "synthesis",
            "state": "stale",
            "reason": "synthesis.stale.render_artifacts_missing",
            **details,
        }
    return {"stage": "synthesis", "state": "stale", "reason": "synthesis.profile.invalid"}


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
    if desired.profile is None:
        prepared = audiobook_export_stage.prepare_audiobook_export(
            project,
            title=desired.title,
            author=desired.author,
            cover=desired.cover,
            bitrate=desired.bitrate,
        )
        target = audiobook_export_stage.audiobook_export_target(project, desired.output)
    else:
        resolved = audiobook_export_stage._resolve_audiobook_profile(
            project,
            desired.profile,
            title=desired.title,
            author=desired.author,
            cover=desired.cover,
            bitrate=desired.bitrate,
        )
        prepared = audiobook_export_stage.prepare_audiobook_export(
            project,
            title=resolved.metadata.get("title"),
            author=resolved.metadata.get("artist"),
            cover=resolved.resolved_output.cover,
            bitrate=resolved.resolved_output.bitrate,
        )
        metadata_tags = dict(resolved.metadata)
        metadata_tags["title"] = prepared.metadata.title
        if prepared.metadata.author is None:
            metadata_tags.pop("artist", None)
        else:
            metadata_tags["artist"] = prepared.metadata.author
        prepared = replace(
            prepared,
            export_id=audiobook_export_stage._profile_audiobook_export_identity(
                prepared, resolved, metadata_tags
            ),
        )
        target = audiobook_export_stage._profile_audiobook_target(project, desired.output, resolved)
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
        path = stored_path if stored_path.is_absolute() else project.state_root / stored_path
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
    desired_settings = project_settings_from_manifest(project.manifest, project.state_root)
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
        state_path = project.paths["composition_state"]
        desired_composition = desired_settings.composition
        if state_path.is_file() and project.paths["composition_master"].is_file():
            try:
                options = {}
                if desired_composition is not None:
                    options = {
                        "mastering": desired_composition.mastering,
                        "target_lufs": desired_composition.target_lufs,
                        "true_peak_ceiling_dbtp": desired_composition.true_peak_ceiling_dbtp,
                        "peak_policy": desired_composition.peak_policy,
                        "clip_policy": desired_composition.clip_policy,
                        "output_sample_rate": desired_composition.sample_rate,
                    }
                state = read_json(state_path)
                if composition_is_current(project, **options):
                    composition = {
                        "stage": "composition",
                        "state": "current",
                        "reason": "current",
                        "job_id": state.get("job_id"),
                        "composition_id": state.get("job_id"),
                    }
                else:
                    composition = {
                        "stage": "composition",
                        "state": "stale",
                        "reason": (
                            "composition.stale.project_settings_changed"
                            if desired_composition is not None
                            else "composition.stale.timing_changed"
                        ),
                    }
            except (OSError, TypeError, ValueError, KeyError):
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
                    path = (
                        stored_path
                        if stored_path.is_absolute()
                        else project.state_root / stored_path
                    )
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
                target = resolve_project_export_target(
                    project,
                    audio_format=audio_format,
                    bitrate=desired_export.bitrate,
                    output=desired_export.output,
                    profile=desired_export.profile,
                    format_explicit=desired_export.format_explicit,
                )
                if is_export_current(
                    project,
                    target,
                    audio_format=audio_format,
                    bitrate=desired_export.bitrate,
                    profile=desired_export.profile,
                    format_explicit=desired_export.format_explicit,
                ):
                    state = output_state_for(project, target)
                    output = {
                        "stage": "output",
                        "state": "current",
                        "reason": "current",
                        "format": str(state.get("audio_format")) if state else audio_format,
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
                            stored_path
                            if stored_path.is_absolute()
                            else project.state_root / stored_path
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
        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            AudioExportIntegrationError,
            AudioExportUnavailableError,
        ):
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
        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            audiobook_export_stage.AudiobookExportError,
            AudioExportIntegrationError,
            AudioExportUnavailableError,
        ):
            output = {"stage": "output", "state": "stale", "reason": "output.invalid"}
    stages = [*rows, synthesis, composition, output]
    issues = [issue for row in stages if (issue := _stage_issue(row)) is not None]
    workspace = rows[1].get("workspace")
    if isinstance(workspace, dict) and workspace.get("status") == "dirty":
        dirty_count = int(workspace.get("dirty_chapter_count", 0))
        refresh_command = str(workspace.get("refresh_command", "ssmdconvert book refresh ."))
        issues.append(
            {
                "code": "workspace.dirty",
                "stage": "document",
                "message": (
                    f"SSMD book workspace: dirty; {dirty_count} chapter digest(s) differ from "
                    f"the manifest. Run: {refresh_command}"
                ),
                "details": workspace,
            }
        )
    commands = {
        "plan": "readio plan build .",
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
    planning_attempt: dict[str, Any] | None = None
    try:
        plan_inspection = inspect_plan_state(project, attempt="latest")
        attempt_ref = plan_inspection.get("attempt")
        if isinstance(attempt_ref, Mapping):
            planning_attempt = dict(attempt_ref)
            attempt_status = attempt_ref.get("status")
            if attempt_status in {"blocked", "incomplete"}:
                reason = (
                    f"Latest planning attempt {attempt_ref.get('attempt_id')} is {attempt_status}."
                )
                issues.append(
                    {
                        "code": f"planning.attempt.{attempt_status}",
                        "stage": "plan",
                        "message": reason,
                        "details": attempt_ref,
                    }
                )
                next_actions.insert(
                    0,
                    {
                        "stage": "plan",
                        "command": "readio plan inspect .",
                        "reason": reason,
                    },
                )
    except ProjectPlanAttemptError as exc:
        reason = str(exc)
        issues.append(
            {
                "code": "planning.attempt.invalid",
                "stage": "plan",
                "message": reason,
                "details": exc.details,
            }
        )
        next_actions.insert(
            0,
            {
                "stage": "plan",
                "command": "readio plan inspect .",
                "reason": reason,
            },
        )
    return {
        "project": str(project.root),
        "name": project.manifest.name,
        "source": {"path": project.manifest.source_path, "format": project.manifest.source_format},
        "stages": stages,
        "planning_attempt": planning_attempt,
        "issues": issues,
        "next_actions": next_actions[:1],
    }


def build_project(
    project: Project,
    cfg: Any,
    request: ProjectBuildRequest | None,
    *,
    on_synthesis_event: Callable[[Any], None] | None = None,
    on_planning_progress: PlanningProgressCallback | None = None,
    on_composition_progress: CompositionProgressCallback | None = None,
    on_phase: Callable[[str], None] | None = None,
    on_stage: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    saved = project_settings_from_manifest(project.manifest, project.state_root)
    if request is None:
        request = ProjectBuildRequest(
            composition=saved.composition or CompositionOptions(),
            export=saved.export or ExportOptions(),
        )
    else:
        request = replace(
            request,
            export=merge_export_options(saved.export, request.export),
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
        planned = plan_project(project, cfg, on_progress=on_planning_progress)
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
    composition_options = request.composition
    state_path = project.paths["composition_state"]
    state = read_json(state_path) if state_path.is_file() else {}
    composition_current = composition_is_current(
        project,
        mastering=composition_options.mastering,
        target_lufs=composition_options.target_lufs,
        true_peak_ceiling_dbtp=composition_options.true_peak_ceiling_dbtp,
        peak_policy=composition_options.peak_policy,
        clip_policy=composition_options.clip_policy,
        output_sample_rate=composition_options.sample_rate,
    )
    if composition_current:
        operations.append(
            {
                "stage": "composition",
                "action": "skipped",
                "mastering_profile": state.get("mastering_profile", composition_options.mastering),
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
    target = resolve_project_export_target(
        project,
        audio_format=audio_format,
        bitrate=export_options.bitrate,
        output=export_options.output,
        profile=export_options.profile,
        format_explicit=export_options.format_explicit,
    )
    current_output = is_export_current(
        project,
        target,
        audio_format=audio_format,
        bitrate=export_options.bitrate,
        profile=export_options.profile,
        format_explicit=export_options.format_explicit,
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
            profile=export_options.profile,
            format_explicit=export_options.format_explicit,
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
    on_planning_progress: PlanningProgressCallback | None = None,
    on_composition_progress: CompositionProgressCallback | None = None,
    on_phase: Callable[[str], None] | None = None,
    on_stage: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    request = ProjectBuildRequest(
        target="export",
        selection=selector,
        synthesis=synthesis or SynthesisRequest(),
        composition=CompositionOptions(target_lufs=target_lufs),
        export=ExportOptions(format=cast(AudioFormat, audio_format)),
    )
    return build_project(
        project,
        cfg,
        request,
        on_synthesis_event=on_synthesis_event,
        on_planning_progress=on_planning_progress,
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
    options = composition or CompositionOptions(target_lufs=target_lufs)
    job, _identity = build_audio_job(
        project,
        mastering=options.mastering,
        target_lufs=options.target_lufs,
        true_peak_ceiling_dbtp=options.true_peak_ceiling_dbtp,
        peak_policy=options.peak_policy,
        clip_policy=options.clip_policy,
        output_sample_rate=options.sample_rate,
    )
    result = compose_job(job, on_progress=on_composition_progress)
    if output is not None:
        if on_phase is not None:
            on_phase("Writing preview artifacts")
        output.parent.mkdir(parents=True, exist_ok=True)
        write_pcm16_wav(output, result.audio, result.sample_rate)
    return {
        "profile_id": synthesis["profile"].profile_id,
        "plan_ids": synthesis["plan_ids"],
        "reused": synthesis["reused"],
        "rendered": synthesis["rendered"],
        "activated": activate,
        "sample_rate": result.sample_rate,
        "frames": len(result.audio),
        "items": len(result.items),
        "output": output,
        "composition_id": job.job_id,
        "warnings": [],
        "loudness": _loudness_payload(result.loudness, options.mastering),
    }


__all__ = ["build_project", "preview_project", "project_status", "render_project"]
