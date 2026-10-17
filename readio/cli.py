"""Compact command line for direct speech and .ssmdbook orchestration."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import audiocompose  # type: ignore[import-not-found]
import utterplan  # type: ignore[import-not-found]

from . import __version__, api
from .book import BookBuildRequest, BookBuildResult, BookProgressEvent
from .cli_config import ConfigurationError, add_config_commands, run_config_command
from .cli_control import CLIUsageError, add_control_commands, run_control_command
from .cli_document import add_document_command, run_document_command
from .doctor import doctor_report, format_doctor_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="readio", description="Speech convenience and book orchestration"
    )
    parser.add_argument("--version", action="version", version=f"Readio {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    add_control_commands(commands)
    add_config_commands(commands)
    speak = commands.add_parser("speak", help="Speak literal text through VoiceRender")
    add_document_command(commands)
    speak.add_argument("text", nargs="+")
    speak.add_argument("--voice", help="VoiceRender selector, such as inflect:micro-v2/default")
    speak.add_argument("--engine")
    speak.add_argument("--target")
    speak.add_argument("--lang")
    speak.add_argument("--speaker")
    speak.add_argument("--offline", action="store_true")
    speak.add_argument("--refresh", action="store_true")
    speak.add_argument("--engine-option", action="append", default=[], metavar="KEY=VALUE")

    speak.add_argument("--profile", type=Path)
    speak.add_argument("--role")
    speak.add_argument("--output", type=Path)
    speak.add_argument("--no-play", action="store_true")
    speak.add_argument("--json", action="store_true")
    audiobook = commands.add_parser("audiobook", help="Convert and build .ssmdbook workspaces")
    audiobook_commands = audiobook.add_subparsers(dest="audiobook_command", required=True)

    convert = audiobook_commands.add_parser("convert", help="Convert an EPUB to .ssmdbook")
    convert.add_argument("source", type=Path)
    convert.add_argument("--output", type=Path)
    convert.add_argument("--chapters", default="all")
    convert.add_argument("--lang")
    convert.add_argument("--metadata", action="append", default=[], metavar="KEY=VALUE")
    convert.add_argument("--sequence-fallback-mode")
    convert.add_argument("--speech-mode", choices=("freeze", "preserve"))
    convert.add_argument("--spacy")
    convert.add_argument("--no-progress", action="store_true")
    convert.add_argument("--json", action="store_true")

    build = audiobook_commands.add_parser("build", help="Run selected .ssmdbook specialist stages")
    build.add_argument("workspace", type=Path)
    build.add_argument("--profile", type=Path)
    build.add_argument("--chapters", default="all")
    build.add_argument("--language")
    build.add_argument(
        "--diagnostics", choices=("errors", "warnings", "all", "none"), default="errors"
    )
    build.add_argument("--speech", default="all", metavar="SELECTOR")
    build.add_argument("--renderplan-root", type=Path)
    build.add_argument("--voicerender-output-root", type=Path)
    build.add_argument("--cache-dir", type=Path)
    build.add_argument("--reset-stale", action="store_true")
    build.add_argument(
        "--render-emphasis-policy",
        choices=("reject", "defer-to-compose"),
        default="defer-to-compose",
    )
    build.add_argument("--compose-renderplan-root", type=Path)
    build.add_argument("--compose-voicerender-root", type=Path)
    build.add_argument("--compose-output-root", type=Path)
    build.add_argument("--media-root", type=Path)
    build.add_argument("--save-audiojobs", type=Path)
    build.add_argument("--compose-sample-rate", type=int)
    build.add_argument("--compose-channels", type=int)
    build.add_argument("--clip-policy", choices=("clamp", "warn", "error"))
    build.add_argument("--target-lufs", type=float)
    build.add_argument("--true-peak-ceiling-dbtp", type=float)
    build.add_argument("--peak-policy", choices=("reduce_gain", "error"))
    build.add_argument(
        "--compose-emphasis-policy",
        choices=("segment_fallback", "strict"),
        default="segment_fallback",
    )
    build.add_argument("--variant")
    build.add_argument("--force", action="store_true")
    build.add_argument("--fail-fast", action="store_true")
    build.add_argument(
        "--stop-after",
        choices=("compile", "render-plan", "synth", "compose", "export"),
    )
    build.add_argument("--no-compile", action="store_true")
    build.add_argument("--no-render-plan", action="store_true")
    build.add_argument("--no-synth", action="store_true")
    build.add_argument("--no-compose", action="store_true")
    build.add_argument("--no-export", action="store_true")
    build.add_argument("--output", type=Path)
    build.add_argument("--export-profile", type=Path)
    build.add_argument("--cover", type=Path)
    build.add_argument("--bitrate")
    build.add_argument("--metadata", action="append", default=[], metavar="KEY=VALUE")
    build.add_argument("--export-sample-rate", type=int)
    build.add_argument("--export-channels", type=int)
    build.add_argument("--ffmpeg", type=Path)
    build.add_argument("--ffprobe", type=Path)
    build.add_argument("--json", action="store_true")
    build.add_argument("--no-progress", action="store_true")

    doctor = commands.add_parser("doctor", help="Report specialist package and engine health")
    doctor.add_argument("--json", action="store_true")
    return parser


def _engine_options(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        key, separator, option_value = value.partition("=")
        if not separator or not key:
            raise CLIUsageError(f"engine option must use KEY=VALUE: {value!r}")
        result[key] = option_value
    return result


def _metadata_assignments(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        key, separator, content = value.partition("=")
        if not separator or not key:
            raise CLIUsageError(f"metadata must use KEY=VALUE: {value!r}")
        result[key] = content
    return result


def _progress(event: BookProgressEvent) -> None:
    current = getattr(event, "current", None)
    total = getattr(event, "total", None)
    details = f" {current}/{total}" if current is not None and total is not None else ""
    message = f" {event.message}" if getattr(event, "message", None) else ""
    print(f"{event.stage}: {event.kind.removeprefix('stage.')}{details}{message}", file=sys.stderr)


def _build_summary(result: BookBuildResult) -> dict[str, Any]:
    stage_results = (
        ("compile", "compile_result"),
        ("render-plan", "renderplan_result"),
        ("synth", "voicerender_result"),
        ("compose", "compose_result"),
        ("export", "export_result"),
    )
    return {
        "workspace": str(result.workspace),
        "variant": result.variant,
        "completed_stages": [
            name for name, field in stage_results if getattr(result, field) is not None
        ],
    }


def _run_speak(args: argparse.Namespace) -> int:
    if args.profile is not None and any(
        value is not None for value in (args.voice, args.engine, args.target)
    ):
        raise CLIUsageError("--profile is mutually exclusive with --voice, --engine, and --target")
    if args.role is not None and args.profile is None:
        raise CLIUsageError("--role requires --profile")
    rendered = api.speak(
        " ".join(args.text),
        voice=args.voice,
        engine=args.engine,
        target=args.target,
        language=args.lang,
        speaker=args.speaker,
        offline=args.offline,
        refresh=args.refresh,
        engine_options=_engine_options(args.engine_option),
        profile=args.profile,
        role=args.role,
        output=args.output,
        no_play=args.no_play,
    )
    if args.json:
        audio = getattr(rendered, "audio", None)
        result = {
            "output": str(args.output) if args.output is not None else None,
            "sample_rate": getattr(rendered, "sample_rate", None),
            "frames": len(audio) if audio is not None else None,
            "played": not args.no_play,
        }
        print(json.dumps({"ok": True, "result": result}))
    elif args.output is not None:
        print(f"WAV: {args.output}")
    elif args.no_play:
        print("Speech synthesized; playback skipped.")
    return 0


def _run_convert(args: argparse.Namespace) -> int:
    workspace = api.convert_book(
        args.source,
        args.output,
        chapters=args.chapters,
        language=args.lang,
        metadata=_metadata_assignments(args.metadata),
        sequence_fallback_mode=args.sequence_fallback_mode,
        speech_mode=args.speech_mode,
        spacy=args.spacy,
        on_progress=None if args.no_progress else _progress,
    )
    result = {"workspace": str(workspace.path)}
    if args.json:
        print(json.dumps(result))
    else:
        print(f"Created .ssmdbook workspace: {workspace.path}")
    return 0


def _run_build(args: argparse.Namespace) -> int:
    compose_values = {
        "sample_rate": args.compose_sample_rate,
        "channels": args.compose_channels,
        "clip_policy": args.clip_policy,
        "target_lufs": args.target_lufs,
        "true_peak_ceiling_dbtp": args.true_peak_ceiling_dbtp,
        "peak_policy": args.peak_policy,
    }
    compose_options = (
        audiocompose.BookComposeOptions(**compose_values)
        if any(value is not None for value in compose_values.values())
        else None
    )
    compile_request = utterplan.BookCompileRequest(
        book=args.workspace,
        chapters=args.chapters,
        language=args.language,
        force=args.force,
        fail_fast=args.fail_fast,
        diagnostics=args.diagnostics,
    )
    render_request = (
        utterplan.BookRenderRequest(
            book=args.workspace,
            profile=args.profile,
            chapters=args.chapters,
            variant=args.variant,
            force=args.force,
            fail_fast=args.fail_fast,
        )
        if args.profile is not None
        else None
    )
    request = BookBuildRequest(
        workspace=args.workspace,
        profile=args.profile,
        chapters=args.chapters,
        compile=not args.no_compile,
        render_plan=not args.no_render_plan,
        synthesize=not args.no_synth,
        compose=not args.no_compose,
        export=not args.no_export,
        force=args.force,
        fail_fast=args.fail_fast,
        variant=args.variant,
        stop_after=args.stop_after,
        compile_request=compile_request,
        render_request=render_request,
        speech=args.speech,
        renderplan_root=args.renderplan_root,
        voicerender_output_root=args.voicerender_output_root,
        cache_dir=args.cache_dir,
        reset_stale_output=args.reset_stale,
        render_emphasis_policy=args.render_emphasis_policy,
        compose_options=compose_options,
        compose_renderplan_root=args.compose_renderplan_root,
        compose_voicerender_root=args.compose_voicerender_root,
        compose_output_root=args.compose_output_root,
        media_root=args.media_root,
        save_audiojobs=args.save_audiojobs,
        compose_emphasis_policy=args.compose_emphasis_policy,
        output=args.output,
        export_profile=args.export_profile,
        export_metadata=_metadata_assignments(args.metadata),
        cover=args.cover,
        bitrate=args.bitrate,
        export_sample_rate=args.export_sample_rate,
        export_channels=args.export_channels,
        ffmpeg=args.ffmpeg,
        ffprobe=args.ffprobe,
    )
    result = api.build_book(request, on_progress=None if args.no_progress else _progress)
    summary = _build_summary(result)
    if args.json:
        print(json.dumps(summary))
    else:
        print(f"Workspace: {summary['workspace']}")
        print(f"Variant: {summary['variant'] or 'not projected'}")
        print(f"Completed: {', '.join(summary['completed_stages']) or 'none'}")
    return 0


def _report_error(error: Exception, args: argparse.Namespace, *, status: int) -> int:
    if getattr(args, "json", False):
        details = {
            "type": type(error).__name__,
            "code": getattr(error, "code", None),
            "message": str(error),
            "stage": getattr(error, "stage", None),
        }
        print(json.dumps({"ok": False, "error": details}), file=sys.stdout)
    else:
        print(f"readio: {error}", file=sys.stderr)
    return status


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "speak":
            return _run_speak(args)
        if args.command == "render":
            return run_document_command(args, api)
        if args.command == "doctor":
            report = doctor_report()
            print(json.dumps(report, indent=2) if args.json else format_doctor_report(report))
            return 0
        if args.command == "config":
            return run_config_command(args)
        if args.command in {"engines", "models", "voices", "voice", "profile"}:
            return run_control_command(args, api)
        if args.audiobook_command == "convert":
            return _run_convert(args)
        if args.audiobook_command == "build":
            return _run_build(args)
    except ConfigurationError as error:
        return _report_error(error, args, status=2)
    except CLIUsageError as error:
        return _report_error(error, args, status=2)
    except Exception as error:  # noqa: BLE001
        return _report_error(error, args, status=1)
    raise AssertionError(f"unhandled command: {args.command}")


__all__ = ["build_parser", "main"]
