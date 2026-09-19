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
    if not trace_path.is_file() or not project.paths["plan_index"].is_file():
        return {"stage": "synthesis", "state": "stale", "reason": "synthesis.missing"}
    try:
        plan = load_scope_plan(project)
        trace = read_json(trace_path)
        units = {str(item["unit_id"]): item for item in trace.get("units", [])}
        reusable = 0
        for unit in plan.units:
            item = units.get(unit.id)
            if item and item.get("content_hash") == unit.content_hash:
                path = project.root / str(item.get("path", ""))
                if path.is_file() and hash_file(path) == item.get("audio_sha256"):
                    reusable += 1
        if reusable == len(plan.units):
            return {
                "stage": "synthesis",
                "state": "current",
                "reason": "current",
                "reusable": reusable,
                "total": len(plan.units),
            }
        return {
            "stage": "synthesis",
            "state": "stale",
            "reason": "synthesis.stale.content_changed",
            "reusable": reusable,
            "total": len(plan.units),
        }
    except (OSError, KeyError, TypeError, ValueError):
        return {"stage": "synthesis", "state": "stale", "reason": "synthesis.invalid"}


def project_status(project: Project) -> dict[str, Any]:
    rows = semantic_status(project)
    plan_current = rows[-1]["state"] == "current"
    synthesis = (
        _synthesis_status(project)
        if plan_current
        else {"stage": "synthesis", "state": "stale", "reason": "synthesis.stale.plan_changed"}
    )
    composition = {"stage": "composition", "state": "stale", "reason": "composition.missing"}
    state_path = project.paths["composition_state"]
    if state_path.is_file() and project.paths["composition_master"].is_file():
        try:
            state = read_json(state_path)
            if hash_file(project.paths["composition_master"]) == state.get("master_sha256"):
                composition = {
                    "stage": "composition",
                    "state": "current",
                    "reason": "current",
                    "composition_id": state.get("composition_id"),
                }
        except (OSError, ValueError):
            pass
    output = {"stage": "output", "state": "stale", "reason": "output.missing"}
    output_state = project.root / "output" / "state.json"
    if output_state.is_file():
        try:
            state = read_json(output_state)
            path = project.root / str(state["path"])
            if path.is_file() and hash_file(path) == state.get("output_sha256"):
                output = {
                    "stage": "output",
                    "state": "current",
                    "reason": "current",
                    "format": state.get("audio_format"),
                }
        except (OSError, KeyError, ValueError):
            pass
    return {"project": str(project.root), "stages": [*rows, synthesis, composition, output]}


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
) -> dict[str, Any]:
    synthesis = synthesize_project(
        project, cfg, request=request, selector=selector, activate=activate
    )
    result = compose_artifacts(synthesis["artifacts"], target_lufs=target_lufs, output=output)
    return {
        "profile_id": synthesis["profile"].profile_id,
        "reused": synthesis["reused"],
        "rendered": synthesis["rendered"],
        "activated": activate,
        **result,
    }


__all__ = ["preview_project", "project_status", "render_project"]
