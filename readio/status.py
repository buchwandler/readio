"""In-memory, read-only aggregation of specialist-owned bundle status."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Any, cast

from readio.api.types import (
    BundleStatus,
    NextAction,
    StageStatus,
    StatusRequest,
    StatusStage,
    StatusState,
    VariantStatus,
)
from readio.diagnostics import log_event, logger_for
from readio.integrations import status as providers
from readio.workspace import WorkspaceRef

_STATES = {
    "not_started",
    "partial",
    "complete",
    "stale",
    "blocked",
    "unavailable",
    "unknown",
    "error",
}

_LOGGER = logger_for("status")


def _get(value: object, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _plain(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return f"<{type(value).__name__}>"


def _details(value: object) -> dict[str, object]:
    details = _get(value, "details", {})
    return (
        {str(key): _plain(item) for key, item in details.items()}
        if isinstance(details, Mapping)
        else {}
    )


def _status(
    stage: StatusStage,
    value: object | None,
    *,
    package: str,
    absent_state: StatusState = "unknown",
    absent_summary: str | None = None,
    state_map: Mapping[str, StatusState] | None = None,
) -> StageStatus:
    if value is None:
        missing = StageStatus(
            stage,
            absent_state,
            absent_summary or f"{package} does not expose a read-only inspector.",
            package=package,
        )
        log_event(
            _LOGGER,
            logging.DEBUG,
            "status.normalized",
            stage=stage,
            package=package,
            state=missing.state,
        )
        return missing
    raw_state = _get(value, "state", "unknown")
    if isinstance(raw_state, Enum):
        raw_state = raw_state.value
    if not isinstance(raw_state, str):
        raw_state = "unknown"
    normalized = (state_map or {}).get(raw_state, raw_state)
    state: StatusState = cast(StatusState, normalized if normalized in _STATES else "unknown")
    summary = _get(value, "summary")
    if not isinstance(summary, str) or not summary:
        summary = f"{package} reports {raw_state}."
    current = _get(value, "current")
    total = _get(value, "total")
    if type(current) is not int:
        current = None
    if type(total) is not int:
        total = None
    reported_package = _get(value, "package")
    normalized_status = StageStatus(
        stage=stage,
        state=state,
        summary=summary,
        current=current,
        total=total,
        package=reported_package if isinstance(reported_package, str) else package,
        details=_details(value),
    )
    log_event(
        _LOGGER,
        logging.DEBUG,
        "status.normalized",
        stage=stage,
        package=normalized_status.package,
        state=normalized_status.state,
        current=normalized_status.current,
        total=normalized_status.total,
    )
    return normalized_status


def _error_status(stage: StatusStage, package: str, error: Exception) -> StageStatus:
    return StageStatus(
        stage=stage,
        state="error",
        summary=f"{package} status inspection failed.",
        package=package,
        details={"error_type": type(error).__name__, "error": str(error)},
    )


def _blocked(stage: StatusStage, prerequisite: str) -> StageStatus:
    return StageStatus(stage, "blocked", f"Waiting for {prerequisite}.")


def _variant_items(result: object) -> tuple[object, ...]:
    values = _get(result, "variants", ())
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        return tuple(values)
    return ()


def _variant_status(
    item: object,
    request: StatusRequest,
    *,
    compose_report: object | None,
    compose_error: Exception | None = None,
    export_result: object | None = None,
    export_error: Exception | None = None,
) -> VariantStatus:
    variant_value = _get(item, "name", _get(item, "variant"))
    variant = variant_value if isinstance(variant_value, str) else ""
    plan_state = _get(item, "plan_status", "unknown")
    if plan_state == "valid":
        plan_status = StageStatus(
            "render_plan",
            "complete",
            f"RenderPlan available for {variant}.",
            package="voicerender",
            details={"profile_id": _plain(_get(item, "profile_id"))},
        )
    elif plan_state in {"not_started", "not_rendered"}:
        plan_status = StageStatus(
            "render_plan",
            "not_started",
            "No projected RenderPlan variant exists.",
            package="voicerender",
        )
    elif plan_state == "invalid":
        plan_status = StageStatus(
            "render_plan",
            "error",
            "RenderPlan validation failed.",
            package="voicerender",
            details={"error": str(_get(item, "validation_error", "invalid RenderPlan"))},
        )
    else:
        plan_status = StageStatus(
            "render_plan",
            "unknown",
            "VoiceRender could not determine RenderPlan state.",
            package="voicerender",
        )

    render = _status(
        "render",
        item,
        package="voicerender",
        state_map={"not_rendered": "not_started", "failed": "error"},
    )
    render_details = dict(render.details)
    for key in (
        "completed_units",
        "total_units",
        "available_speech",
        "error_code",
        "error_message",
    ):
        value = _get(item, key)
        if value is not None:
            render_details[key] = _plain(value)
    completed = _get(item, "completed_units")
    total = _get(item, "total_units")
    if render.state in {"complete", "partial"} and type(completed) is int and type(total) is int:
        summary = f"{completed}/{total} units complete."
    else:
        summary = render.summary
    render = StageStatus(
        render.stage,
        render.state,
        summary,
        current=completed if type(completed) is int else None,
        total=total if type(total) is int else None,
        package=render.package,
        details=render_details,
    )

    if render.state != "complete":
        compose = _blocked("compose", "render")
    elif compose_error is not None:
        compose = _error_status("compose", "audiocompose", compose_error)
    else:
        compose = _status("compose", compose_report, package="audiocompose")

    if providers.AUDIOEXPORT_UNAVAILABLE is export_result:
        export = StageStatus(
            "export", "unavailable", "AudioExport is not installed.", package="audioexport"
        )
    elif compose.state != "complete":
        export = _blocked("export", "compose")
    elif export_error is not None:
        export = _error_status("export", "audioexport", export_error)
    else:
        export = _status("export", export_result, package="audioexport")

    return VariantStatus(variant, request.profile, plan_status, render, compose, export)


def inspect_status(
    workspace: WorkspaceRef | str | Path,
    request: StatusRequest | None = None,
) -> BundleStatus:
    """Inspect specialist state without writing files or synthesizing stage truth."""
    request = request or StatusRequest()
    root = workspace.root if isinstance(workspace, WorkspaceRef) else Path(workspace)
    root = root.expanduser().resolve()
    native_bundle = providers.inspect_bundle(WorkspaceRef(root, "document"))
    kind = _get(native_bundle, "kind")
    if isinstance(kind, Enum):
        kind = kind.value
    if kind not in {"document", "book"}:
        raise ValueError(f"SSMDConvert returned unsupported bundle kind {kind!r}")
    workspace_ref = WorkspaceRef(root, kind)

    warnings: list[str] = []
    try:
        compile_report = providers.inspect_compile(workspace_ref, request.chapters)
        compile_status = _status(
            "compile",
            compile_report,
            package="utterplan",
            absent_summary="UtterPlan does not expose a read-only compile inspector.",
        )
    except Exception as error:  # noqa: BLE001 - Preserve partial status on stage-specific failures.
        compile_status = _error_status("compile", "utterplan", error)
        warnings.append(f"UtterPlan inspection failed: {error}")

    variant_discovery_failed = False
    try:
        variants_result = providers.inspect_variants(workspace_ref)
        variants = _variant_items(variants_result)
    except Exception as error:  # noqa: BLE001 - Status remains useful when one provider fails.
        variants = ()
        variant_discovery_failed = True
        warnings.append(f"VoiceRender inspection failed: {error}")
        if request.variant is not None:
            variants = (
                {"name": request.variant, "plan_status": "unknown", "render_status": "unknown"},
            )

    if request.variant is not None and not variant_discovery_failed:
        variants = tuple(
            item
            for item in variants
            if _get(item, "name", _get(item, "variant")) == request.variant
        )
        if not variants:
            variants = (
                {
                    "name": request.variant,
                    "plan_status": "not_started",
                    "render_status": "not_rendered",
                },
            )

    variant_statuses: list[VariantStatus] = []
    for item in variants:
        name = _get(item, "name", _get(item, "variant"))
        if not isinstance(name, str):
            warnings.append("VoiceRender returned a variant without a string identity; skipped it.")
            continue
        compose_report: object | None = None
        compose_error: Exception | None = None
        if _get(item, "render_status") in {"complete", "reused"}:
            try:
                compose_report = providers.inspect_compose(workspace_ref, name, request.chapters)
            except Exception as error:  # noqa: BLE001
                compose_error = error
                warnings.append(f"AudioCompose inspection failed for {name}: {error}")

        export_report: object | None = None
        export_error: Exception | None = None
        if not providers.audioexport_installed():
            export_report = providers.AUDIOEXPORT_UNAVAILABLE
        elif compose_report is not None and _get(compose_report, "state") == "complete":
            try:
                export_report = providers.inspect_export(workspace_ref, name, request.chapters)
            except Exception as error:  # noqa: BLE001
                export_error = error
                warnings.append(f"AudioExport inspection failed for {name}: {error}")

        variant_statuses.append(
            _variant_status(
                item,
                request,
                compose_report=compose_report,
                compose_error=compose_error,
                export_result=export_report,
                export_error=export_error,
            )
        )

    if compile_status.state != "complete":
        next_actions = _compile_actions(workspace_ref, request, compile_status)
    elif variant_discovery_failed:
        next_actions = ()
    else:
        next_actions = _variant_actions(workspace_ref, request, tuple(variant_statuses))

    return BundleStatus(
        workspace=workspace_ref,
        compile=compile_status,
        variants=tuple(variant_statuses),
        next_actions=next_actions,
        warnings=tuple(warnings),
    )


def _specialist_flags(status: StageStatus) -> tuple[tuple[str, ...], bool]:
    flags = status.details.get("recommended_flags", ())
    if not isinstance(flags, (tuple, list)) or any(not isinstance(flag, str) for flag in flags):
        flags = ()
    destructive = status.details.get("destructive") is True
    return tuple(flags), destructive


def _compile_actions(
    workspace: WorkspaceRef, request: StatusRequest, status: StageStatus
) -> tuple[NextAction, ...]:
    if status.state not in {"not_started", "partial", "stale"}:
        return ()
    flags, destructive = _specialist_flags(status)
    argv = (
        "readio",
        "bundle",
        "compile",
        str(workspace.root),
        "--chapters",
        request.chapters,
        *flags,
    )
    return (NextAction("compile", status.summary, argv, destructive=destructive),)


def _variant_actions(
    workspace: WorkspaceRef,
    request: StatusRequest,
    variants: tuple[VariantStatus, ...],
) -> tuple[NextAction, ...]:
    if not variants:
        return (_render_plan_action(workspace, request),)

    actions: list[NextAction] = []
    for variant in variants:
        if variant.render_plan.state in {"not_started", "partial", "stale"}:
            actions.append(_render_plan_action(workspace, request))
            continue
        if variant.render_plan.state != "complete":
            continue
        if variant.render.state in {"not_started", "partial", "stale"}:
            flags, destructive = _specialist_flags(variant.render)
            argv = (
                "readio",
                "bundle",
                "render",
                str(workspace.root),
                "--variant",
                variant.variant,
                "--chapters",
                request.chapters,
                *flags,
            )
            actions.append(
                NextAction("render", variant.render.summary, argv, destructive=destructive)
            )
            continue
        if variant.render.state != "complete":
            continue
        if variant.compose.state in {"not_started", "partial", "stale"}:
            flags, destructive = _specialist_flags(variant.compose)
            argv = (
                "readio",
                "bundle",
                "compose",
                str(workspace.root),
                "--variant",
                variant.variant,
                "--chapters",
                request.chapters,
                *flags,
            )
            actions.append(
                NextAction("compose", variant.compose.summary, argv, destructive=destructive)
            )
            continue
        if variant.compose.state == "complete" and variant.export.state in {
            "not_started",
            "partial",
            "stale",
        }:
            output = variant.export.details.get("output")
            if isinstance(output, str) and output:
                argv = (
                    "readio",
                    "bundle",
                    "export",
                    str(workspace.root),
                    "--variant",
                    variant.variant,
                    "--chapters",
                    request.chapters,
                    "--output",
                    output,
                )
                actions.append(NextAction("export", variant.export.summary, argv))
            else:
                actions.append(
                    NextAction(
                        "export",
                        variant.export.summary,
                        (),
                        requires=("output",),
                    )
                )
    return tuple(actions)


def _render_plan_action(workspace: WorkspaceRef, request: StatusRequest) -> NextAction:
    if request.profile is None:
        return NextAction(
            "render_plan",
            "A RenderProfile is required before projection.",
            (),
            requires=("profile",),
        )
    argv = (
        "readio",
        "bundle",
        "render-plan",
        str(workspace.root),
        "--profile",
        str(request.profile),
        "--chapters",
        request.chapters,
    )
    return NextAction("render_plan", "Create a profile-specific RenderPlan.", argv)


__all__ = ["inspect_status"]
