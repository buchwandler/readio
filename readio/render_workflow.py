"""Unified file-to-audio planning and execution for Readio."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from readio.api.types import (
    ComposeRequest,
    ExportRequest,
    PlanRequest,
    RenderPlanRequest,
    RenderRequest,
    RenderResult,
    VoiceRenderRequest,
    WorkflowEvent,
    WorkflowPlan,
    WorkflowStage,
)
from readio.errors import (
    OutputCollisionError,
    WorkflowError,
    WorkflowResolutionError,
    WorkflowStageError,
    WorkflowStateError,
)
from readio.integrations.audiocompose import inspect_bundle_composition
from readio.integrations.audioexport import inspect_bundle_export, supported_formats
from readio.integrations.ssmdconvert import inspect_source, inspect_source_provenance
from readio.integrations.utterplan import inspect_bundle_compile, inspect_bundle_projection
from readio.integrations.voicerender import inspect_bundle_render, load_render_profile
from readio.intent import _default_output_path
from readio.workflow import make_workflow_plan, normalize_inspection_state, plan_workflow_step
from readio.workspace import (
    CreateWorkspaceRequest,
    WorkspacePolicy,
    resolve_workspace,
    resolve_workspace_policy,
)

_STAGE_ORDER: tuple[WorkflowStage, ...] = (
    "convert",
    "compile",
    "render_plan",
    "render",
    "compose",
    "export",
)
_STAGE_INDEX = {stage: index for index, stage in enumerate(_STAGE_ORDER)}
_STAGE_PACKAGES = {
    "convert": "SSMDConvert",
    "compile": "UtterPlan",
    "render_plan": "UtterPlan",
    "render": "VoiceRender",
    "compose": "AudioCompose",
    "export": "AudioExport",
}


@dataclass(frozen=True, slots=True)
class _ResolvedRender:
    source: Path
    profile: Path
    policy: WorkspacePolicy
    source_kind: str
    format: str
    output: Path
    expected_extension: str
    warnings: tuple[str, ...]


def _value(report: object, name: str, default: Any = None) -> Any:
    if isinstance(report, Mapping):
        return report.get(name, default)
    return getattr(report, name, default)


def _replacement_safe(report: object) -> bool:
    value = _value(report, "replacement_safe", _value(report, "reset_safe", False))
    return value is True


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(directory.resolve(strict=False))
    except ValueError:
        return False
    return True


def _resolve(request: RenderRequest) -> _ResolvedRender:
    try:
        source = Path(request.source).expanduser().resolve(strict=True)
        profile = Path(request.profile).expanduser().resolve(strict=True)
        policy = resolve_workspace_policy(source, bundle=request.bundle)
    except Exception as exc:
        raise WorkflowResolutionError(
            f"Cannot resolve render source, profile, or workspace policy: {exc}", cause=exc
        ) from exc
    if not profile.is_file():
        error = FileNotFoundError(profile)
        raise WorkflowResolutionError(
            f"RenderProfile is not a regular file: {profile}", cause=error
        )
    if request.rebuild_from not in {None, *_STAGE_ORDER}:
        raise WorkflowResolutionError(f"Unknown rebuild stage: {request.rebuild_from!r}")
    if request.planning.force and request.rebuild_from != "compile":
        raise WorkflowResolutionError(
            "planning.force requires rebuild_from='compile'; use explicit workflow rebuild controls."
        )
    if policy.source_is_bundle and request.rebuild_from == "convert":
        raise WorkflowResolutionError(
            "rebuild_from='convert' is invalid when SOURCE is already a bundle"
        )

    try:
        source_inspection = inspect_source(policy.root if policy.source_is_bundle else source)
        source_kind = source_inspection.kind
        if source_kind not in {"book", "document"}:
            raise ValueError(f"SSMDConvert returned unsupported source kind {source_kind!r}")
        load_render_profile(profile)
        formats = supported_formats()
    except Exception as exc:
        raise WorkflowResolutionError(f"Cannot inspect render inputs: {exc}", cause=exc) from exc

    selected_format = request.format or "auto"
    if not isinstance(selected_format, str) or not selected_format.strip():
        raise WorkflowResolutionError("output format must be a non-empty string")
    selected_format = selected_format.casefold()
    if selected_format == "auto":
        selected_format = "m4b" if source_kind == "book" else "mp3"
    if selected_format not in formats:
        raise WorkflowResolutionError(
            f"AudioExport does not support output format {selected_format!r}"
        )
    if source_kind == "book" and selected_format != "m4b":
        raise WorkflowResolutionError("book sources can only be rendered as m4b audiobooks")
    if source_kind == "document" and selected_format == "m4b":
        raise WorkflowResolutionError("m4b output requires a book source")
    extension = getattr(formats[selected_format], "extension", None)
    if not isinstance(extension, str) or not extension.startswith("."):
        raise WorkflowResolutionError(
            "AudioExport returned a format without a valid file extension"
        )

    try:
        requested_output = (
            Path(request.output).expanduser()
            if request.output is not None
            else _default_output_path(source, source_kind, selected_format)
        )
        output = requested_output.resolve(strict=False)
        if output.suffix.casefold() != extension.casefold():
            raise WorkflowResolutionError(
                f"output path must use the {extension} extension for format {selected_format!r}"
            )
    except Exception as exc:
        raise WorkflowResolutionError(
            f"Cannot resolve final output path: {exc}", cause=exc
        ) from exc
    if output == source:
        raise WorkflowResolutionError("final output must not overwrite the source")
    if policy.mode == "persistent" and _is_within(output, policy.root):
        raise WorkflowResolutionError("final output must be outside the persistent bundle")
    warnings: list[str] = []
    if policy.source_is_bundle and request.bundle is not None:
        explicit = Path(request.bundle).expanduser().resolve(strict=False)
        if explicit != policy.root:
            warnings.append("SOURCE is already a bundle; the separate bundle option is ignored.")
    return _ResolvedRender(
        source=source,
        profile=profile,
        policy=policy,
        source_kind=source_kind,
        format=selected_format,
        output=output,
        expected_extension=extension.casefold(),
        warnings=tuple(warnings),
    )


def _selector(request: RenderRequest, report: object | None = None) -> str:
    selected = _value(report, "recommended_selector") if report is not None else None
    if isinstance(selected, str) and selected.strip():
        return selected
    return request.chapters


def _deferred(reason: str, report: object | None = None) -> dict[str, object]:
    return {
        "state": "not_started",
        "reason": reason,
        "inspection_deferred": True,
        "prior_inspection": report,
    }


def _all_projection_items_waiting(report: object) -> bool:
    items = _value(report, "items", ())
    states = [getattr(item, "state", None) for item in items]
    return bool(states) and all(state in {"not_ready", "complete"} for state in states)


def _plan_step(
    request: RenderRequest,
    stage: WorkflowStage,
    inspection: object,
    *,
    selector: str | None = None,
    replacement_safe: bool | None = None,
    explicit_rebuild: bool | None = None,
) -> Any:
    if explicit_rebuild is None:
        start = (
            _STAGE_INDEX[request.rebuild_from]
            if request.rebuild_from is not None
            else len(_STAGE_ORDER)
        )
        explicit_rebuild = _STAGE_INDEX[stage] >= start
    safe = _replacement_safe(inspection) if replacement_safe is None else replacement_safe
    return plan_workflow_step(
        stage,
        _STAGE_PACKAGES[stage],
        inspection,
        selector=selector,
        explicit_rebuild=explicit_rebuild,
        replacement_safe=safe,
    )


def _export_request(
    resolved: _ResolvedRender, request: RenderRequest, variant: str, chapters: str
) -> ExportRequest:
    options = request.export
    return ExportRequest(
        variant=variant,
        chapters=chapters,
        output=resolved.output,
        format=resolved.format,
        bitrate=request.bitrate,
        sample_rate=request.sample_rate,
        channels=request.channels,
        metadata=options.metadata,
        cover=options.cover,
        timeline=options.timeline,
        audiocompose_root=options.audiocompose_root,
        output_root=options.output_root,
        ffmpeg=options.ffmpeg,
        ffprobe=options.ffprobe,
    )


def _inspect_with_policy(
    resolved: _ResolvedRender, request: RenderRequest, policy: WorkspacePolicy
) -> WorkflowPlan:
    existing = policy.source_is_bundle or policy.root.exists()
    if policy.source_is_bundle:
        conversion_inspection: object = {
            "state": "complete",
            "reason": "SOURCE is already an editable SSMD bundle; conversion is not applicable.",
            "replacement_safe": False,
        }
    elif not existing:
        conversion_inspection = {
            "state": "not_started",
            "reason": "No persistent bundle exists; conversion will create the selected workspace.",
        }
    else:
        try:
            conversion_inspection = inspect_source_provenance(resolved.source, policy.root)
        except Exception as exc:  # noqa: BLE001 - fail closed on source provenance inspection errors
            conversion_inspection = {"state": "error", "error": str(exc), "reason": str(exc)}

    conversion_step = _plan_step(
        request,
        "convert",
        conversion_inspection,
        selector="all"
        if policy.mode == "persistent" and resolved.source_kind == "book"
        else request.chapters,
    )
    steps = [conversion_step]
    variant = request.variant

    if not existing:
        for stage in _STAGE_ORDER[1:]:
            report: object = {
                "state": "not_started",
                "reason": "The workspace will be created during this workflow run.",
            }
            if stage == "export" and (resolved.output.exists() or resolved.output.is_symlink()):
                safe_path = resolved.output.is_file() and not resolved.output.is_symlink()
                report = {
                    "state": "stale",
                    "force_required": True,
                    "replacement_safe": safe_path,
                    "reason": "The final output exists and has no bundle export identity to reuse.",
                }
            if stage == "export":
                output_step = _plan_step(
                    request,
                    stage,
                    report,
                    selector=request.chapters,
                    replacement_safe=(request.replace_output and _replacement_safe(report)),
                    explicit_rebuild=(
                        request.rebuild_from == "export"
                        or (
                            request.replace_output and normalize_inspection_state(report) == "stale"
                        )
                    ),
                )
                steps.append(output_step)
            else:
                steps.append(_plan_step(request, stage, report, selector=request.chapters))
        return make_workflow_plan(
            resolved.source,
            policy,
            variant=variant,
            output=resolved.output,
            steps=tuple(steps),
            warnings=resolved.warnings,
        )

    try:
        workspace = resolve_workspace(policy.root)
    except Exception as exc:  # noqa: BLE001 - convert invalid workspace state into a blocked plan
        error_report = {"state": "error", "error": str(exc), "reason": str(exc)}
        for stage in _STAGE_ORDER[1:]:
            steps.append(_plan_step(request, stage, error_report, selector=request.chapters))
        return make_workflow_plan(
            resolved.source,
            policy,
            variant=variant,
            output=resolved.output,
            steps=tuple(steps),
            warnings=resolved.warnings,
        )

    compile_report: object
    try:
        compile_report = inspect_bundle_compile(
            workspace,
            chapters=request.chapters,
            language=request.language,
            planner_config=request.planning.planner_config,
        )
    except Exception as exc:  # noqa: BLE001 - fail closed on UtterPlan inspection errors
        compile_report = {"state": "error", "error": str(exc), "reason": str(exc)}
    compile_step = _plan_step(request, "compile", compile_report, selector=request.chapters)
    steps.append(compile_step)
    compile_pending = compile_step.action in {"execute", "replace"}

    try:
        projection_report: object = inspect_bundle_projection(
            workspace,
            profile=resolved.profile,
            chapters=request.chapters,
            variant=request.variant,
            pause_timing=request.projection.pause_timing,
            output_root=request.composition.renderplan_root,
        )
        if (
            compile_pending
            and normalize_inspection_state(projection_report) == "blocked"
            and _all_projection_items_waiting(projection_report)
            and _value(projection_report, "profile_snapshot_mismatch") is not True
        ):
            projection_report = _deferred(
                "Projection inspection awaits the FlowPlans scheduled by the compile stage.",
                projection_report,
            )
    except Exception as exc:  # noqa: BLE001 - fail closed or defer until compile completes
        projection_report = (
            _deferred(
                "Projection inspection will be retried after compilation.", {"error": str(exc)}
            )
            if compile_pending
            else {"state": "error", "error": str(exc), "reason": str(exc)}
        )
    variant = _value(projection_report, "variant", variant)
    projection_step = _plan_step(
        request,
        "render_plan",
        projection_report,
        selector=request.chapters,
        explicit_rebuild=(
            (
                request.rebuild_from is not None
                and _STAGE_INDEX["render_plan"] >= _STAGE_INDEX[request.rebuild_from]
            )
            or (
                normalize_inspection_state(projection_report) == "stale"
                and _replacement_safe(projection_report)
            )
        ),
    )
    steps.append(projection_step)
    projection_pending = projection_step.action in {"execute", "replace"}

    if not isinstance(variant, str) or not variant:
        render_report: object = _deferred("Render inspection awaits a resolved RenderPlan variant.")
    else:
        try:
            render_request = VoiceRenderRequest(
                variant=variant,
                chapters=request.chapters,
                speech=request.voice_render.speech,
                renderplan_root=request.composition.renderplan_root,
                output_root=request.composition.voicerender_root,
                cache_dir=request.voice_render.cache_dir,
                fail_fast=request.voice_render.fail_fast,
                emphasis_policy=request.voice_render.emphasis_policy,
            )
            render_report = inspect_bundle_render(workspace, render_request)
        except Exception as exc:  # noqa: BLE001 - fail closed or defer until projection completes
            render_report = (
                _deferred(
                    "Render inspection will be retried after RenderPlan projection.",
                    {"error": str(exc)},
                )
                if projection_pending
                else {"state": "error", "error": str(exc), "reason": str(exc)}
            )
    render_step = _plan_step(request, "render", render_report, selector=request.chapters)
    steps.append(render_step)
    render_pending = render_step.action in {"execute", "replace"}

    if not isinstance(variant, str) or not variant:
        compose_report: object = _deferred("Composition inspection awaits a resolved variant.")
    else:
        try:
            compose_report = inspect_bundle_composition(
                workspace,
                variant=variant,
                chapters=request.chapters,
                renderplan_root=request.composition.renderplan_root,
                voicerender_root=request.composition.voicerender_root,
                output_root=request.composition.output_root,
            )
            if render_pending and normalize_inspection_state(compose_report) == "blocked":
                compose_report = _deferred(
                    "Composition inspection will be retried after rendering.", compose_report
                )
        except Exception as exc:  # noqa: BLE001 - fail closed or defer until rendering completes
            compose_report = (
                _deferred(
                    "Composition inspection will be retried after rendering.", {"error": str(exc)}
                )
                if render_pending
                else {"state": "error", "error": str(exc), "reason": str(exc)}
            )
    compose_step = _plan_step(request, "compose", compose_report, selector=request.chapters)
    steps.append(compose_step)
    compose_pending = compose_step.action in {"execute", "replace"}

    if not isinstance(variant, str) or not variant:
        export_report: object = _deferred("Export inspection awaits a resolved variant.")
    else:
        try:
            export_report = inspect_bundle_export(
                workspace, _export_request(resolved, request, variant, request.chapters)
            )
            if compose_pending and normalize_inspection_state(export_report) == "error":
                export_report = _deferred(
                    "Export inspection will be retried after composition.", export_report
                )
        except Exception as exc:  # noqa: BLE001 - fail closed or defer until composition completes
            export_report = (
                _deferred(
                    "Export inspection will be retried after composition.", {"error": str(exc)}
                )
                if compose_pending
                else {"state": "error", "error": str(exc), "reason": str(exc)}
            )
    if (
        resolved.output.exists()
        and normalize_inspection_state(export_report) != "complete"
        and _value(export_report, "inspection_deferred") is True
    ):
        safe_output = resolved.output.is_file() and not resolved.output.is_symlink()
        export_report = {
            "state": "stale",
            "force_required": True,
            "replacement_safe": safe_output,
            "reason": "The existing final output cannot be verified until upstream composition completes.",
        }
    if resolved.output.is_symlink() or (resolved.output.exists() and not resolved.output.is_file()):
        export_report = {
            "state": "error",
            "reason": "final output must be a regular non-symlink file path",
            "replacement_safe": False,
        }
    export_state = normalize_inspection_state(export_report)
    output_step = _plan_step(
        request,
        "export",
        export_report,
        selector=request.chapters,
        replacement_safe=request.replace_output and _replacement_safe(export_report),
        explicit_rebuild=(
            request.rebuild_from == "export" or (request.replace_output and export_state == "stale")
        ),
    )
    steps.append(output_step)
    return make_workflow_plan(
        resolved.source,
        policy,
        variant=variant if isinstance(variant, str) and variant else None,
        output=resolved.output,
        steps=tuple(steps),
        warnings=resolved.warnings,
    )


def inspect_render_workflow(request: RenderRequest) -> WorkflowPlan:
    """Return a specialist-backed convergence plan without creating or changing files."""
    resolved = _resolve(request)
    return _inspect_with_policy(resolved, request, resolved.policy)


def _raise_if_blocked(plan: WorkflowPlan) -> None:
    blocked = [step for step in plan.steps if step.action == "blocked"]
    if not blocked:
        return
    message = "; ".join(f"{step.stage}: {step.reason}" for step in blocked)
    details = {
        "blocked_stages": tuple(step.stage for step in blocked),
        "steps": tuple(
            {
                "stage": step.stage,
                "action": step.action,
                "state": step.state,
                "reason": step.reason,
            }
            for step in blocked
        ),
    }
    if blocked[0].stage == "export" and blocked[0].state in {"stale", "complete"}:
        raise OutputCollisionError(
            f"Final output {plan.output} requires explicit replace_output=True.",
            details=details,
        )
    raise WorkflowStateError(f"Render workflow cannot safely converge: {message}", details=details)


def _emit(
    callback: Callable[[WorkflowEvent], None] | None,
    stage: WorkflowStage,
    kind: str,
    step: Any,
) -> None:
    if callback is not None:
        callback(
            WorkflowEvent(
                stage=stage,
                kind=kind,
                package=step.package,
                selector=step.selector,
                message=step.reason,
                details={"action": step.action, "state": step.state},
            )
        )


def _invoke_stage(
    service: Any,
    request: RenderRequest,
    resolved: _ResolvedRender,
    policy: WorkspacePolicy,
    step: Any,
    variant: str | None,
) -> Any:
    stage = step.stage
    selector = step.selector or request.chapters
    if stage == "convert":
        chapters = (
            "all"
            if policy.mode == "persistent" or resolved.source_kind != "book"
            else request.chapters
        )
        return service.create_workspace(
            resolved.source,
            CreateWorkspaceRequest(
                source=resolved.source,
                output=policy.root,
                language=request.language,
                chapters=chapters,
                preparation=request.preparation,
                force=step.force,
            ),
        )
    workspace = resolve_workspace(policy.root)
    if stage == "compile":
        return service.plan(
            workspace,
            PlanRequest(
                chapters=selector,
                language=request.language,
                planner_config=request.planning.planner_config,
                diagnostics=request.planning.diagnostics,
                force=step.force,
            ),
        )
    if stage == "render_plan":
        result = service.render_plan(
            workspace,
            RenderPlanRequest(
                profile=resolved.profile,
                variant=request.variant,
                chapters=selector,
                output_root=request.composition.renderplan_root,
                pause_timing=request.projection.pause_timing,
                force=step.force,
            ),
        )
        return result
    if not isinstance(variant, str) or not variant:
        raise WorkflowResolutionError(f"{stage} requires a resolved RenderPlan variant")
    if stage == "render":
        return service.render(
            workspace,
            VoiceRenderRequest(
                variant=variant,
                chapters=selector,
                speech=request.voice_render.speech,
                renderplan_root=request.composition.renderplan_root,
                output_root=request.composition.voicerender_root,
                cache_dir=request.voice_render.cache_dir,
                force=step.force,
                reset_stale_output=step.force,
                fail_fast=request.voice_render.fail_fast,
                emphasis_policy=request.voice_render.emphasis_policy,
            ),
        )
    if stage == "compose":
        options = request.composition
        return service.compose(
            workspace,
            ComposeRequest(
                variant=variant,
                chapters=selector,
                pause_policy=options.pause_policy,
                output_policy=options.output_policy,
                options=options.options,
                renderplan_root=options.renderplan_root,
                voicerender_root=options.voicerender_root,
                output_root=options.output_root,
                media_root=options.media_root,
                save_audiojobs=options.save_audiojobs,
                force=step.force,
                fail_fast=options.fail_fast,
                emphasis_policy=options.emphasis_policy,
            ),
        )
    if stage == "export":
        return service.export(
            workspace,
            replace(
                _export_request(resolved, request, variant, selector),
                force=request.replace_output,
            ),
        )
    raise WorkflowResolutionError(f"Unsupported workflow stage {stage!r}")


def _execute_stages(
    service: Any,
    request: RenderRequest,
    resolved: _ResolvedRender,
    policy: WorkspacePolicy,
    on_progress: Callable[[WorkflowEvent], None] | None,
) -> RenderResult:
    plan = _inspect_with_policy(resolved, request, policy)
    _raise_if_blocked(plan)
    executed: list[str] = []
    reused: list[str] = []
    variant = plan.variant

    for stage in _STAGE_ORDER:
        current = _inspect_with_policy(resolved, request, policy)
        _raise_if_blocked(current)
        step = next(item for item in current.steps if item.stage == stage)
        if step.action == "reuse":
            reused.append(stage)
            _emit(on_progress, stage, "stage.reused", step)
            continue
        if step.action not in {"execute", "replace"}:
            raise WorkflowStateError(
                f"Unexpected workflow action {step.action!r} for {stage}",
                details={"stage": stage, "action": step.action, "state": step.state},
            )
        _emit(on_progress, stage, "stage.started", step)
        try:
            result = _invoke_stage(service, request, resolved, policy, step, variant)
        except WorkflowError:
            raise
        except Exception as exc:
            raise WorkflowStageError(
                stage,
                str(exc),
                package=step.package,
                source=resolved.source,
                workspace=policy.root,
                variant=variant,
                selector=step.selector,
                cause=exc,
                details={"action": step.action, "state": step.state},
            ) from exc
        executed.append(stage)
        if stage == "render_plan":
            candidate = _value(result, "variant")
            if isinstance(candidate, str) and candidate:
                variant = candidate
        updated = _inspect_with_policy(resolved, request, policy)
        _raise_if_blocked(updated)
        if stage == "render_plan" and updated.variant:
            variant = updated.variant
        verified = next(item for item in updated.steps if item.stage == stage)
        if verified.state != "complete":
            raise WorkflowStateError(
                f"{stage} completed without reaching a reusable complete state: {verified.reason}",
                details={
                    "stage": stage,
                    "state": verified.state,
                    "action": verified.action,
                    "details": dict(verified.details),
                },
            )
        _emit(on_progress, stage, "stage.completed", verified)

    if not isinstance(variant, str) or not variant:
        final_plan = _inspect_with_policy(resolved, request, policy)
        variant = final_plan.variant
    if not isinstance(variant, str) or not variant:
        raise WorkflowStateError("Render workflow completed without a resolved variant")
    return RenderResult(
        output=resolved.output,
        workspace=None if policy.mode == "temporary" else policy.root,
        workspace_mode=policy.mode,
        variant=variant,
        executed_stages=tuple(executed),
        reused_stages=tuple(reused),
        warnings=resolved.warnings,
    )


def render_workflow(
    service: Any,
    request: RenderRequest,
    *,
    dry_run: bool = False,
    on_progress: Callable[[WorkflowEvent], None] | None = None,
) -> WorkflowPlan | RenderResult:
    """Inspect or execute a complete source-to-audio workflow."""
    resolved = _resolve(request)
    initial_plan = _inspect_with_policy(resolved, request, resolved.policy)
    if dry_run:
        return initial_plan
    _raise_if_blocked(initial_plan)
    if resolved.policy.mode == "temporary":
        with TemporaryDirectory(prefix="readio-render-") as temporary_root:
            temporary_path = Path(temporary_root).resolve()
            if _is_within(resolved.output, temporary_path):
                raise OutputCollisionError(
                    "final output must be outside the temporary workflow workspace"
                )
            runtime_policy = replace(
                resolved.policy,
                root=temporary_path / resolved.policy.root.name,
                newly_created=True,
                cleanup=True,
            )
            return _execute_stages(service, request, resolved, runtime_policy, on_progress)
    return _execute_stages(service, request, resolved, resolved.policy, on_progress)


__all__ = ["inspect_render_workflow", "render_workflow"]
