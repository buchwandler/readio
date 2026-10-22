"""CLI for native `.ssmd-bundle` workspace orchestration."""

from __future__ import annotations

import argparse
import json
import logging
import shlex
import sys
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import audiocompose  # type: ignore[import-not-found]
import utterplan  # type: ignore[import-not-found]

from . import __version__, api
from .cli_control import CLIUsageError, add_control_commands, run_control_command
from .diagnostics import configure_diagnostics, log_event, logger_for, timed_span
from .doctor import doctor_report, format_doctor_report


def _extract_global_verbosity(argv: Sequence[str]) -> tuple[list[str], int]:
    """Remove verbosity switches globally, stopping at the literal ``--``."""
    remaining: list[str] = []
    verbosity = 0
    iterator = iter(argv)
    for token in iterator:
        if token == "--":
            remaining.append(token)
            remaining.extend(iterator)
            break
        if token in {"-v", "--verbose"}:
            verbosity += 1
        elif token == "-vv":
            verbosity += 2
        else:
            remaining.append(token)
    return remaining, min(verbosity, 2)


def _command_name(args: argparse.Namespace) -> str:
    parts = [str(args.command)]
    for attribute in (
        "bundle_command",
        "engine_command",
        "model_command",
        "voices_command",
        "voice_command",
        "profile_command",
        "role_command",
        "source_command",
    ):
        value = getattr(args, attribute, None)
        if value:
            parts.append(str(value))
    return ".".join(parts)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="readio",
        description="Orchestrate speech workflows in native .ssmd-bundle workspaces",
    )
    parser.add_argument("--version", action="version", version=f"Readio {__version__}")
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="increase diagnostic verbosity; repeat for DEBUG (-vv)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    add_control_commands(commands)
    _add_bundle_commands(commands)
    _add_speak_command(commands)
    doctor = commands.add_parser("doctor", help="Report package and VoiceRender engine health")
    doctor.add_argument("--json", action="store_true")
    return parser


def _add_bundle_commands(commands: Any) -> None:
    bundle = commands.add_parser(
        "bundle",
        help="Convert and process .ssmd-bundle workspaces",
        description="Lifecycle commands for editable .ssmd-bundle directories.",
    )
    stages = bundle.add_subparsers(dest="bundle_command", required=True)
    _add_bundle_convert_command(stages)
    _add_bundle_compile_command(stages)
    _add_bundle_render_plan_command(stages)
    _add_bundle_render_command(stages)
    _add_bundle_compose_command(stages)
    _add_bundle_export_command(stages)
    _add_bundle_run_command(stages)
    _add_bundle_status_command(stages)


def _add_bundle_status_command(commands: Any) -> None:
    command = commands.add_parser(
        "status",
        help="Inspect bundle pipeline state and show the next runnable command",
    )
    command.add_argument("bundle", type=Path)
    command.add_argument("--chapters", default="all")
    command.add_argument("--profile", type=Path)
    command.add_argument("--variant")
    command.add_argument("--json", action="store_true")


def _add_bundle_convert_command(commands: Any) -> None:
    command = commands.add_parser(
        "convert",
        help="Convert source material into an SSMD bundle",
        description="Convert source material into an editable .ssmd-bundle directory.",
    )
    command.add_argument("source", type=Path)
    command.add_argument("-o", "--output", type=Path)
    command.add_argument("--chapters", default="all")
    command.add_argument("--language")
    command.add_argument("--force", action="store_true")
    command.add_argument("--json", action="store_true")
    command.add_argument("--no-progress", action="store_true")


def _add_bundle_compile_command(commands: Any) -> None:
    command = commands.add_parser("compile", help="Compile bundle speech with UtterPlan")
    command.add_argument("bundle", type=Path)
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


def _add_bundle_render_plan_command(commands: Any) -> None:
    command = commands.add_parser("render-plan", help="Project a profile-specific RenderPlan")
    command.add_argument("bundle", type=Path)
    command.add_argument("--profile", type=Path, required=True)
    command.add_argument("--variant", help="User-friendly variant label")
    command.add_argument("--chapters", default="all")
    command.add_argument("--utterplan-root", type=Path)
    command.add_argument("--output-root", type=Path)
    command.add_argument("--force", action="store_true")
    command.add_argument("--json", action="store_true")
    command.add_argument("--no-progress", action="store_true")


def _add_bundle_render_command(commands: Any) -> None:
    command = commands.add_parser("render", help="Render speech through VoiceRender")
    command.add_argument("bundle", type=Path)
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
    command.add_argument("--target-lufs", type=float)
    command.add_argument("--true-peak-ceiling-dbtp", type=float)
    command.add_argument("--clip-policy", choices=("clamp", "warn", "error"))
    command.add_argument("--peak-policy", choices=("reduce_gain", "error"))


def _add_bundle_compose_command(commands: Any) -> None:
    command = commands.add_parser("compose", help="Compose rendered segments through AudioCompose")
    command.add_argument("bundle", type=Path)
    command.add_argument("--variant", required=True)
    command.add_argument("--chapters", default="all")
    command.add_argument("--renderplan-root", type=Path)
    command.add_argument("--voicerender-root", type=Path)
    command.add_argument("--output-root", type=Path)
    command.add_argument("--media-root", type=Path)
    command.add_argument("--save-audiojobs", type=Path)
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


def _add_bundle_export_command(commands: Any) -> None:
    command = commands.add_parser("export", help="Export composed audio through AudioExport")
    command.add_argument("bundle", type=Path)
    command.add_argument("--variant", required=True)
    command.add_argument("--chapters", default="all")
    command.add_argument("--profile", type=Path, help="AudioExport audiobook profile")
    command.add_argument("--format")
    command.add_argument("--timeline", type=Path)
    command.add_argument("--sample-rate", type=int)
    command.add_argument("--channels", type=int)
    _add_export_options(command)
    command.add_argument("--json", action="store_true")


def _add_bundle_run_command(commands: Any) -> None:
    command = commands.add_parser("run", help="Run the complete SSMD bundle pipeline")
    command.add_argument("source_or_bundle", type=Path)
    command.add_argument("--bundle-output", type=Path)
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
    command.add_argument("--save-audiojobs", type=Path)
    _add_compose_options(command)
    command.add_argument("--channels", type=int)
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


def _format_stage_status(status: api.StageStatus, *, indent: str = "") -> str:
    return f"{indent}{status.stage:<14} {status.state:<12} {status.summary}".rstrip()


def _format_bundle_status(result: api.BundleStatus, chapters: str) -> str:
    lines = [
        f"Bundle: {result.workspace.root}",
        f"Kind: {result.workspace.kind}",
        f"Scope: {chapters} chapters",
        "",
        "PIPELINE",
        _format_stage_status(result.compile),
    ]
    for variant in result.variants:
        lines.extend(
            [
                "",
                f"VARIANT {variant.variant}",
                _format_stage_status(variant.render_plan, indent="  "),
                _format_stage_status(variant.render, indent="  "),
                _format_stage_status(variant.compose, indent="  "),
                _format_stage_status(variant.export, indent="  "),
            ]
        )
    if result.next_actions:
        lines.extend(["", "NEXT"])
        for action in result.next_actions:
            if action.argv:
                label = "Next (will replace stale output):" if action.destructive else "Next:"
                lines.extend([label, f"  {shlex.join(action.argv)}"])
            elif "profile" in action.requires:
                lines.extend(
                    [
                        f"Next: {action.reason}",
                        (
                            "  readio bundle render-plan "
                            f"{shlex.quote(str(result.workspace.root))} --profile <PROFILE> --chapters "
                            f"{shlex.quote(chapters)}"
                        ),
                    ]
                )
            else:
                requirement = ", ".join(action.requires) or "additional context"
                lines.append(f"Next: {action.reason} (requires {requirement}).")
    elif (
        result.compile.state == "complete"
        and result.variants
        and all(
            item.render_plan.state == item.render.state == item.compose.state == "complete"
            for item in result.variants
        )
    ):
        lines.extend(["", "Core pipeline complete."])
    else:
        lines.extend(
            ["", "No safe next action could be determined from available specialist state."]
        )
    if result.warnings:
        lines.extend(["", "WARNINGS", *(f"- {warning}" for warning in result.warnings)])
    return "\n".join(lines)


def _compose_request(args: argparse.Namespace, variant: str) -> api.ComposeRequest:
    values = {
        "sample_rate": getattr(args, "sample_rate", None),
        "clip_policy": getattr(args, "clip_policy", None),
        "target_lufs": getattr(args, "target_lufs", None),
        "true_peak_ceiling_dbtp": getattr(args, "true_peak_ceiling_dbtp", None),
        "peak_policy": getattr(args, "peak_policy", None),
    }
    option_values = {key: value for key, value in values.items() if value is not None}
    options = audiocompose.BundleComposeOptions(**option_values) if option_values else None
    renderplan_root = getattr(args, "compose_renderplan_root", None)
    if renderplan_root is None:
        renderplan_root = getattr(args, "renderplan_root", None)
    output_root = getattr(args, "compose_output_root", None)
    if output_root is None:
        output_root = getattr(args, "output_root", None)
    return api.ComposeRequest(
        variant=variant,
        chapters=args.chapters,
        options=options,
        renderplan_root=renderplan_root,
        voicerender_root=getattr(args, "voicerender_root", None),
        output_root=output_root,
        media_root=getattr(args, "media_root", None),
        save_audiojobs=getattr(args, "save_audiojobs", None),
        force=getattr(args, "force_compose", getattr(args, "force", False)),
        fail_fast=getattr(args, "fail_fast", False),
        emphasis_policy=getattr(
            args, "compose_emphasis_policy", getattr(args, "emphasis_policy", "segment_fallback")
        ),
    )


def _export_request(args: argparse.Namespace, variant: str) -> api.ExportRequest:
    profile = getattr(args, "export_profile", None)
    if profile is None:
        profile = getattr(args, "profile", None)
    output_root = getattr(args, "export_output_root", None)
    if output_root is None:
        output_root = getattr(args, "output_root", None)
    return api.ExportRequest(
        variant=variant,
        chapters=args.chapters,
        output=args.output,
        format=getattr(args, "format", None),
        profile=profile,
        bitrate=args.bitrate,
        sample_rate=getattr(args, "sample_rate", None),
        channels=getattr(args, "channels", None),
        metadata=_metadata(getattr(args, "metadata", [])),
        cover=args.cover,
        timeline=getattr(args, "timeline", None),
        audiocompose_root=args.audiocompose_root,
        output_root=output_root,
        force=getattr(args, "force_export", getattr(args, "force", False)),
        ffmpeg=args.ffmpeg,
        ffprobe=args.ffprobe,
    )


def _run_bundle_command(args: argparse.Namespace, service: api.Readio) -> int:
    on_progress = None if getattr(args, "no_progress", False) else _progress

    if args.bundle_command == "status":
        status_result = service.status(
            args.bundle,
            api.StatusRequest(
                chapters=args.chapters,
                profile=args.profile,
                variant=args.variant,
            ),
        )
        return _emit_result(
            status_result,
            as_json=args.json,
            human=_format_bundle_status(status_result, args.chapters),
        )

    if args.bundle_command == "convert":
        create_request = api.CreateWorkspaceRequest(
            source=args.source,
            output=args.output,
            language=args.language,
            chapters=args.chapters,
            force=args.force,
        )
        workspace = service.create_workspace(args.source, create_request, on_progress=on_progress)
        convert_payload = {
            "bundle": str(workspace.root),
            "bundle_kind": workspace.kind,
            "manifest": str(workspace.root / "manifest.json"),
        }
        return _emit_result(
            convert_payload,
            as_json=args.json,
            human=f"Created SSMD bundle: {workspace.root} (kind: {workspace.kind})",
        )

    if args.bundle_command == "compile":
        result = service.plan(
            args.bundle,
            api.PlanRequest(
                chapters=args.chapters,
                language=args.language,
                diagnostics=args.diagnostics,
                planner_config=_planner_config(args),
                force=args.force,
            ),
            on_progress=on_progress,
        )
        return _emit_result(result, as_json=args.json, human="UtterPlan compilation complete.")

    if args.bundle_command == "render-plan":
        projection = service.render_plan(
            args.bundle,
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
        result = {"variant": projection.variant, "result": projection.native}
        return _emit_result(
            result,
            as_json=args.json,
            human=f"RenderPlan variant: {projection.variant}",
        )

    if args.bundle_command == "render":
        result = service.render(
            args.bundle,
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
        return _emit_result(result, as_json=args.json, human="VoiceRender complete.")

    if args.bundle_command == "compose":
        result = service.compose(
            args.bundle, _compose_request(args, args.variant), on_progress=on_progress
        )
        return _emit_result(result, as_json=args.json, human="AudioCompose complete.")

    if args.bundle_command == "export":
        result = service.export(args.bundle, _export_request(args, args.variant))
        return _emit_result(result, as_json=args.json, human="AudioExport complete.")

    if args.bundle_command == "run":
        source = args.source_or_bundle
        run_request = api.RunRequest(
            create=api.CreateWorkspaceRequest(
                source=source,
                output=args.bundle_output,
                language=args.language,
                chapters=args.chapters,
                force=args.force_convert,
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
            compose=_compose_request(args, "pending"),
            export=_export_request(args, "pending") if args.export else None,
        )
        pipeline_result = service.run(source, run_request, on_progress=on_progress)
        pipeline_payload = {
            "bundle": str(pipeline_result.workspace.root),
            "bundle_kind": pipeline_result.workspace.kind,
            "variant": pipeline_result.variant,
            "completed_stages": ["compile", "render-plan", "render", "compose"]
            + (["export"] if pipeline_result.export is not None else []),
            "export": pipeline_result.export,
        }
        return _emit_result(
            pipeline_payload,
            as_json=args.json,
            human=f"Completed pipeline: {pipeline_result.variant}",
        )

    raise AssertionError(f"unknown bundle command {args.bundle_command}")


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
    supplied_argv = list(sys.argv[1:] if argv is None else argv)
    parse_argv, verbosity = _extract_global_verbosity(supplied_argv)
    configure_diagnostics(verbosity)
    parser = build_parser()
    args = parser.parse_args(parse_argv)
    command_name = _command_name(args)
    logger = logger_for("cli")
    log_event(logger, logging.DEBUG, "command.dispatch", command=command_name, verbosity=verbosity)
    try:
        with timed_span(
            logger,
            "command",
            level=logging.DEBUG if verbosity >= 2 else logging.INFO,
            command=command_name,
        ):
            service = api.Readio()
            if args.command == "bundle":
                return _run_bundle_command(args, service)
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
        if verbosity >= 2:
            traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)
        return _report_error(error, args, status=1)
    raise AssertionError(f"unhandled command: {args.command}")


__all__ = ["build_parser", "main"]
