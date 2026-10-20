"""CLI for native `.ssmdoc` and `.ssmdbook` workspace orchestration."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import audiocompose  # type: ignore[import-not-found]
import utterplan  # type: ignore[import-not-found]

from . import __version__, api
from .cli_control import CLIUsageError, add_control_commands, run_control_command
from .doctor import doctor_report, format_doctor_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="readio",
        description="Orchestrate speech workflows in native .ssmdoc and .ssmdbook workspaces",
    )
    parser.add_argument("--version", action="version", version=f"Readio {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    add_control_commands(commands)
    _add_init_command(commands)
    _add_plan_command(commands)
    _add_render_plan_command(commands)
    _add_render_command(commands)
    _add_compose_command(commands)
    _add_export_command(commands)
    _add_run_command(commands)
    _add_speak_command(commands)
    doctor = commands.add_parser("doctor", help="Report package and VoiceRender engine health")
    doctor.add_argument("--json", action="store_true")
    return parser


def _add_init_command(commands: Any) -> None:
    command = commands.add_parser("init", help="Convert source into a native SSMD workspace")
    command.add_argument("source", type=Path)
    command.add_argument("-o", "--output", type=Path)
    command.add_argument("--kind", choices=("auto", "document", "book"), default="auto")
    command.add_argument("--chapters", default="all")
    command.add_argument("--language")
    command.add_argument("--force", action="store_true")
    command.add_argument("--json", action="store_true")
    command.add_argument("--no-progress", action="store_true")


def _add_plan_command(commands: Any) -> None:
    command = commands.add_parser("plan", help="Compile workspace speech with UtterPlan")
    command.add_argument("workspace", type=Path)
    command.add_argument("--chapters", default="all")
    command.add_argument("--language")
    command.add_argument(
        "--diagnostics", choices=("errors", "warnings", "all", "none"), default="errors"
    )
    command.add_argument("--force", action="store_true")
    command.add_argument("--json", action="store_true")
    command.add_argument("--no-progress", action="store_true")
    _add_planner_options(command)


def _add_planner_options(command: Any) -> None:
    command.add_argument("--text-preparation", choices=("auto", "ssmd", "spokenform", "identity"))
    command.add_argument("--overlap-mode", choices=("snap", "strict"))
    command.add_argument("--renderability-mode", choices=("strict", "repair"))
    command.add_argument("--spacy", choices=("auto", "on", "off"))
    command.add_argument("--spacy-model")
    command.add_argument("--spacy-model-size", choices=("sm", "md", "lg", "trf"))
    command.add_argument("--require-spacy", action="store_true")


def _planner_config(args: argparse.Namespace) -> Any | None:
    controls: dict[str, Any] = {
        "text_preparation": args.text_preparation,
        "overlap_mode": args.overlap_mode,
        "renderability_mode": args.renderability_mode,
    }
    controls = {name: value for name, value in controls.items() if value is not None}
    linguistics = None
    if (
        args.spacy is not None
        or args.spacy_model is not None
        or args.spacy_model_size is not None
        or args.require_spacy
    ):
        if args.spacy == "auto":
            use_spacy = None
        elif args.spacy is None:
            use_spacy = True
        else:
            use_spacy = args.spacy == "on"
        linguistics = utterplan.LinguisticsConfig(
            use_spacy=use_spacy,
            spacy_model=args.spacy_model,
            spacy_model_size=args.spacy_model_size,
            require_spacy=args.require_spacy,
        )
        controls["linguistics"] = linguistics
    if not controls:
        return None
    if args.language is None:
        raise CLIUsageError("--language is required when specifying planner controls")
    return utterplan.PlannerConfig(language=args.language, **controls)


def _add_render_plan_command(commands: Any) -> None:
    command = commands.add_parser("render-plan", help="Project a profile-specific RenderPlan")
    command.add_argument("workspace", type=Path)
    command.add_argument("--profile", type=Path, required=True)
    command.add_argument("--variant", help="User-friendly variant label")
    command.add_argument("--chapters", default="all")
    command.add_argument("--utterplan-root", type=Path)
    command.add_argument("--output-root", type=Path)
    command.add_argument("--force", action="store_true")
    command.add_argument("--json", action="store_true")
    command.add_argument("--no-progress", action="store_true")


def _add_render_command(commands: Any) -> None:
    command = commands.add_parser("render", help="Render speech through VoiceRender")
    command.add_argument("workspace", type=Path)
    command.add_argument("--variant", required=True)
    command.add_argument("--chapters", default="all")
    command.add_argument("--speech", default="all")
    command.add_argument("--renderplan-root", type=Path)
    command.add_argument("--output-root", type=Path)
    command.add_argument("--cache-dir", type=Path)
    command.add_argument("--force", action="store_true")
    command.add_argument("--reset-stale-output", action="store_true")
    command.add_argument("--fail-fast", action="store_true")
    command.add_argument("--emphasis-policy", default="reject")
    command.add_argument("--json", action="store_true")
    command.add_argument("--no-progress", action="store_true")


def _add_compose_options(command: Any) -> None:
    command.add_argument("--sample-rate", type=int)
    command.add_argument("--channels", type=int)
    command.add_argument("--target-lufs", type=float)
    command.add_argument("--true-peak-ceiling-dbtp", type=float)
    command.add_argument("--clip-policy", choices=("clamp", "warn", "error"))
    command.add_argument("--peak-policy", choices=("reduce_gain", "error"))


def _add_compose_command(commands: Any) -> None:
    command = commands.add_parser("compose", help="Compose rendered segments through AudioCompose")
    command.add_argument("workspace", type=Path)
    command.add_argument("--variant", required=True)
    command.add_argument("--chapters", default="all")
    command.add_argument("--renderplan-root", type=Path)
    command.add_argument("--voicerender-root", type=Path)
    command.add_argument("--output-root", type=Path)
    command.add_argument("--media-root", type=Path)
    command.add_argument("--save-audiojob", type=Path)
    _add_compose_options(command)
    command.add_argument("--force", action="store_true")
    command.add_argument("--fail-fast", action="store_true")
    command.add_argument(
        "--emphasis-policy", choices=("segment_fallback", "strict"), default="segment_fallback"
    )
    command.add_argument("--json", action="store_true")
    command.add_argument("--no-progress", action="store_true")


def _add_export_options(command: Any) -> None:
    command.add_argument("--output", type=Path)
    command.add_argument("--audiocompose-root", type=Path)
    command.add_argument("--output-root", type=Path)
    command.add_argument("--metadata", action="append", default=[], metavar="KEY=VALUE")
    command.add_argument("--cover", type=Path)
    command.add_argument("--bitrate")
    command.add_argument("--ffmpeg", type=Path)
    command.add_argument("--ffprobe", type=Path)
    command.add_argument("--force", action="store_true")


def _add_export_command(commands: Any) -> None:
    command = commands.add_parser("export", help="Export composed audio through AudioExport")
    command.add_argument("workspace", type=Path)
    command.add_argument("--variant", required=True)
    command.add_argument("--chapters", default="all")
    command.add_argument("--profile", type=Path, help="AudioExport audiobook profile")
    command.add_argument("--format", help="Document output format")
    command.add_argument("--timeline", type=Path)
    command.add_argument("--sample-rate", type=int)
    command.add_argument("--channels", type=int)
    _add_export_options(command)
    command.add_argument("--json", action="store_true")


def _add_run_command(commands: Any) -> None:
    command = commands.add_parser("run", help="Run the complete native workspace pipeline")
    command.add_argument("source_or_workspace", type=Path)
    command.add_argument("--workspace-output", type=Path)
    command.add_argument("--kind", choices=("auto", "document", "book"), default="auto")
    command.add_argument("--profile", type=Path, required=True)
    command.add_argument("--variant", help="User-friendly RenderPlan label")
    command.add_argument("--chapters", default="all")
    command.add_argument("--language")
    command.add_argument("--utterplan-root", type=Path)
    command.add_argument(
        "--diagnostics", choices=("errors", "warnings", "all", "none"), default="errors"
    )
    _add_planner_options(command)
    command.add_argument("--renderplan-root", type=Path)
    command.add_argument("--render-output-root", type=Path)
    command.add_argument("--cache-dir", type=Path)
    command.add_argument("--compose-renderplan-root", type=Path)
    command.add_argument("--voicerender-root", type=Path)
    command.add_argument("--compose-output-root", type=Path)
    command.add_argument("--media-root", type=Path)
    command.add_argument("--save-audiojob", type=Path)
    _add_compose_options(command)
    command.add_argument("--force-render", action="store_true")
    command.add_argument("--force-compose", action="store_true")
    command.add_argument("--force-export", action="store_true")
    command.add_argument("--reset-stale-output", action="store_true")
    command.add_argument("--fail-fast", action="store_true")
    command.add_argument("--speech", default="all")
    command.add_argument("--emphasis-policy", default="reject")
    command.add_argument(
        "--compose-emphasis-policy",
        choices=("segment_fallback", "strict"),
        default="segment_fallback",
    )
    command.add_argument("--export", action="store_true", help="Run the optional AudioExport stage")
    command.add_argument("--output", type=Path)
    command.add_argument("--export-output-root", type=Path)
    command.add_argument("--audiocompose-root", type=Path)
    command.add_argument("--format")
    command.add_argument("--bitrate")
    command.add_argument("--export-profile", type=Path)
    command.add_argument("--cover", type=Path)
    command.add_argument("--timeline", type=Path)
    command.add_argument("--metadata", action="append", default=[], metavar="KEY=VALUE")
    command.add_argument("--ffmpeg", type=Path)
    command.add_argument("--ffprobe", type=Path)
    command.add_argument("--force-convert", action="store_true")
    command.add_argument("--force-plan", action="store_true")
    command.add_argument("--force-render-plan", action="store_true")
    command.add_argument("--json", action="store_true")
    command.add_argument("--no-progress", action="store_true")


def _add_speak_command(commands: Any) -> None:
    command = commands.add_parser(
        "speak", help="Speak one-shot text using a RenderProfile or voice"
    )
    command.add_argument("text", nargs="+")
    command.add_argument("--voice")
    command.add_argument("--engine")
    command.add_argument("--target")
    command.add_argument("--lang")
    command.add_argument("--speaker")
    command.add_argument("--profile", type=Path)
    command.add_argument("--role")
    command.add_argument("--offline", action="store_true")
    command.add_argument("--refresh", action="store_true")
    command.add_argument("--engine-option", action="append", default=[], metavar="KEY=VALUE")
    command.add_argument("--output", type=Path)
    command.add_argument("--no-play", action="store_true")
    command.add_argument("--json", action="store_true")


def _metadata(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        key, separator, content = value.partition("=")
        if not separator or not key:
            raise CLIUsageError(f"metadata must use KEY=VALUE: {value!r}")
        result[key] = content
    return result


def _progress(event: api.PipelineEvent) -> None:
    details = (
        f" {event.current}/{event.total}"
        if event.current is not None and event.total is not None
        else ""
    )
    message = f" {event.message}" if event.message else ""
    print(f"{event.stage}: {message}{details}".rstrip(), file=sys.stderr)


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        return _json_value(vars(value))
    except TypeError:
        return str(value)


def _emit_result(result: Any, *, as_json: bool, human: str | None = None) -> int:
    if as_json:
        print(json.dumps({"ok": True, "result": _json_value(result)}, ensure_ascii=False))
    else:
        print(human if human is not None else str(result))
    return 0


def _compose_request(
    args: argparse.Namespace, workspace_kind: str, variant: str
) -> api.ComposeRequest:
    values = {
        "sample_rate": args.sample_rate,
        "channels": args.channels,
        "clip_policy": args.clip_policy,
        "target_lufs": args.target_lufs,
        "true_peak_ceiling_dbtp": args.true_peak_ceiling_dbtp,
        "peak_policy": args.peak_policy,
    }
    option_values = {key: value for key, value in values.items() if value is not None}
    options = None
    if option_values:
        options_type = (
            audiocompose.BookComposeOptions
            if workspace_kind == "book"
            else audiocompose.DocumentComposeOptions
        )
        options = options_type(**option_values)
    return api.ComposeRequest(
        variant=variant,
        chapters=args.chapters,
        options=options,
        renderplan_root=getattr(args, "renderplan_root", None),
        voicerender_root=getattr(args, "voicerender_root", None),
        output_root=getattr(args, "output_root", None),
        media_root=getattr(args, "media_root", None),
        save_audiojob=getattr(args, "save_audiojob", None),
        force=getattr(args, "force", False),
        fail_fast=getattr(args, "fail_fast", False),
        emphasis_policy=getattr(args, "emphasis_policy", "segment_fallback"),
    )


def _export_request(args: argparse.Namespace, workspace_kind: str, variant: str) -> Any:
    common = {
        "variant": variant,
        "output": args.output,
        "bitrate": args.bitrate,
        "metadata": _metadata(args.metadata),
        "cover": args.cover,
        "audiocompose_root": args.audiocompose_root,
        "output_root": args.output_root,
        "force": args.force,
        "ffmpeg": args.ffmpeg,
        "ffprobe": args.ffprobe,
    }
    if workspace_kind == "book":
        return api.BookExportRequest(
            **common,
            chapters=args.chapters,
            profile=args.profile,
            sample_rate=getattr(args, "sample_rate", None),
            channels=getattr(args, "channels", None),
        )
    return api.DocumentExportRequest(**common, format=args.format, timeline=args.timeline)


def _run_command(args: argparse.Namespace, service: api.Readio) -> int:
    on_progress = None if args.no_progress else _progress
    if args.command == "init":
        init_request = api.CreateWorkspaceRequest(
            source=args.source,
            output=args.output,
            kind=args.kind,
            language=args.language,
            chapters=args.chapters,
            force=args.force,
        )
        workspace = service.create_workspace(args.source, init_request, on_progress=on_progress)
        init_result = {
            "workspace_kind": workspace.kind,
            "workspace": str(workspace.root),
            "manifest": str(workspace.root / "manifest.json"),
        }
        return _emit_result(
            init_result,
            as_json=args.json,
            human=f"Created .ssmd{'book' if workspace.kind == 'book' else 'doc'} workspace: {workspace.root}",
        )

    if args.command == "plan":
        plan_result = service.plan(
            args.workspace,
            api.PlanRequest(
                chapters=args.chapters,
                language=args.language,
                diagnostics=args.diagnostics,
                planner_config=_planner_config(args),
                force=args.force,
            ),
            on_progress=on_progress,
        )
        return _emit_result(plan_result, as_json=args.json, human="UtterPlan compilation complete.")

    if args.command == "render-plan":
        projection_result = service.render_plan(
            args.workspace,
            api.RenderPlanRequest(
                profile=args.profile,
                variant=args.variant,
                chapters=args.chapters,
                utterplan_root=args.utterplan_root,
                output_root=args.output_root,
                force=args.force,
            ),
            on_progress=on_progress,
        )
        payload = {"variant": projection_result.variant, "result": projection_result.native}
        return _emit_result(
            payload, as_json=args.json, human=f"RenderPlan variant: {projection_result.variant}"
        )

    if args.command == "render":
        rendering_result = service.render(
            args.workspace,
            api.VoiceRenderRequest(
                variant=args.variant,
                chapters=args.chapters,
                speech=args.speech,
                renderplan_root=args.renderplan_root,
                output_root=args.output_root,
                cache_dir=args.cache_dir,
                force=args.force,
                reset_stale_output=args.reset_stale_output,
                fail_fast=args.fail_fast,
                emphasis_policy=args.emphasis_policy,
            ),
            on_progress=on_progress,
        )
        return _emit_result(rendering_result, as_json=args.json, human="VoiceRender complete.")

    if args.command == "compose":
        workspace = service.resolve_workspace(args.workspace)
        compose_request = _compose_request(args, workspace.kind, args.variant)
        composition_result = service.compose(workspace, compose_request, on_progress=on_progress)
        return _emit_result(composition_result, as_json=args.json, human="AudioCompose complete.")

    if args.command == "export":
        workspace = service.resolve_workspace(args.workspace)
        export_result = service.export(
            workspace, _export_request(args, workspace.kind, args.variant)
        )
        return _emit_result(export_result, as_json=args.json, human="AudioExport complete.")

    if args.command == "run":
        source = args.source_or_workspace
        kind = args.kind
        if kind == "auto":
            name = source.name.casefold()
            kind = (
                "book"
                if name.endswith(".ssmdbook") or source.suffix.casefold() == ".epub"
                else "document"
            )
        export_request: api.DocumentExportRequest | api.BookExportRequest | None = None
        if args.export:
            common = {
                "variant": "pending",
                "output": args.output,
                "bitrate": args.bitrate,
                "metadata": _metadata(args.metadata),
                "cover": args.cover,
                "audiocompose_root": args.audiocompose_root,
                "output_root": args.export_output_root,
                "force": args.force_export,
                "ffmpeg": args.ffmpeg,
                "ffprobe": args.ffprobe,
            }
            if kind == "book":
                export_request = api.BookExportRequest(
                    **common,
                    chapters=args.chapters,
                    profile=args.export_profile,
                    sample_rate=args.sample_rate,
                    channels=args.channels,
                )
            else:
                export_request = api.DocumentExportRequest(
                    **common,
                    format=args.format,
                    timeline=args.timeline,
                )
        run_request = api.RunRequest(
            create=api.CreateWorkspaceRequest(
                source=source,
                kind=args.kind,
                language=args.language,
                chapters=args.chapters,
                force=args.force_convert,
                output=args.workspace_output,
            ),
            plan=api.PlanRequest(
                chapters=args.chapters,
                language=args.language,
                diagnostics=args.diagnostics,
                planner_config=_planner_config(args),
                force=args.force_plan,
            ),
            render_plan=api.RenderPlanRequest(
                profile=args.profile,
                variant=args.variant,
                chapters=args.chapters,
                utterplan_root=args.utterplan_root,
                output_root=args.renderplan_root,
                force=args.force_render_plan,
            ),
            render=api.VoiceRenderRequest(
                variant="pending",
                chapters=args.chapters,
                speech=args.speech,
                renderplan_root=args.renderplan_root,
                output_root=args.render_output_root,
                cache_dir=args.cache_dir,
                force=args.force_render,
                reset_stale_output=args.reset_stale_output,
                fail_fast=args.fail_fast,
                emphasis_policy=args.emphasis_policy,
            ),
            compose=api.ComposeRequest(
                variant="pending",
                chapters=args.chapters,
                options=_compose_request(args, kind, "pending").options,
                renderplan_root=args.compose_renderplan_root,
                voicerender_root=args.voicerender_root,
                output_root=args.compose_output_root,
                media_root=args.media_root,
                save_audiojob=args.save_audiojob,
                force=args.force_compose,
                fail_fast=args.fail_fast,
                emphasis_policy=args.compose_emphasis_policy,
            ),
            export=export_request,
        )
        pipeline_result = service.run(source, run_request, on_progress=on_progress)
        payload = {
            "workspace_kind": pipeline_result.workspace.kind,
            "workspace": str(pipeline_result.workspace.root),
            "variant": pipeline_result.variant,
            "completed_stages": ["compile", "render-plan", "render", "compose"]
            + (["export"] if pipeline_result.export is not None else []),
            "export": pipeline_result.export,
        }
        return _emit_result(
            payload, as_json=args.json, human=f"Completed pipeline: {pipeline_result.variant}"
        )

    raise AssertionError(f"unknown command {args.command}")


def _run_speak(args: argparse.Namespace) -> int:
    try:
        rendered = api.speak(
            " ".join(args.text),
            voice=args.voice,
            engine=args.engine,
            target=args.target,
            language=args.lang,
            speaker=args.speaker,
            offline=args.offline,
            refresh=args.refresh,
            engine_options=dict(value.split("=", 1) for value in args.engine_option),
            profile=args.profile,
            role=args.role,
            output=args.output,
            no_play=args.no_play,
        )
    except ValueError as error:
        raise CLIUsageError(str(error)) from error
    result = {
        "output": str(args.output) if args.output is not None else None,
        "sample_rate": getattr(rendered, "sample_rate", None),
        "frames": len(getattr(rendered, "audio", ())),
        "played": not args.no_play,
    }
    return _emit_result(result, as_json=args.json, human="Speech complete.")


def _report_error(error: Exception, args: argparse.Namespace, *, status: int) -> int:
    if getattr(args, "json", False):
        details = {
            "type": type(error).__name__,
            "code": getattr(error, "code", None),
            "message": str(error),
            "stage": getattr(error, "stage", None),
        }
        print(json.dumps({"ok": False, "error": details}, ensure_ascii=False))
    else:
        print(f"readio: {error}", file=sys.stderr)
    return status


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    service = api.Readio()
    try:
        if args.command in {"init", "plan", "render-plan", "render", "compose", "export", "run"}:
            return _run_command(args, service)
        if args.command == "speak":
            return _run_speak(args)
        if args.command == "doctor":
            result = doctor_report()
            if args.json:
                print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
            else:
                print(format_doctor_report(result))
            return 0
        if args.command in {"engines", "models", "voices", "voice", "profile"}:
            return run_control_command(args, api)
    except CLIUsageError as error:
        return _report_error(error, args, status=2)
    except Exception as error:  # noqa: BLE001
        return _report_error(error, args, status=1)
    raise AssertionError(f"unhandled command: {args.command}")


__all__ = ["build_parser", "main"]
