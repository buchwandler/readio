"""Compact command line for direct speech and .ssmdbook orchestration."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__, api
from .book import BookBuildRequest, BookBuildResult, BookProgressEvent
from .doctor import doctor_report, format_doctor_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="readio", description="Speech convenience and book orchestration"
    )
    parser.add_argument("--version", action="version", version=f"Readio {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    speak = commands.add_parser("speak", help="Speak literal text through VoiceRender")
    speak.add_argument("text", nargs="+")
    speak.add_argument("--voice", help="VoiceRender selector, such as inflect:micro-v2/default")
    speak.add_argument("--engine")
    speak.add_argument("--target")
    speak.add_argument("--lang", default="en-US")
    speak.add_argument("--speaker")
    speak.add_argument("--offline", action="store_true")
    speak.add_argument("--refresh", action="store_true")
    speak.add_argument("--engine-option", action="append", default=[], metavar="KEY=VALUE")

    audiobook = commands.add_parser("audiobook", help="Convert and build .ssmdbook workspaces")
    audiobook_commands = audiobook.add_subparsers(dest="audiobook_command", required=True)

    convert = audiobook_commands.add_parser("convert", help="Convert an EPUB to .ssmdbook")
    convert.add_argument("source", type=Path)
    convert.add_argument("--output", type=Path)
    convert.add_argument("--chapters", default="all")
    convert.add_argument("--lang")
    convert.add_argument("--json", action="store_true")

    build = audiobook_commands.add_parser("build", help="Run selected .ssmdbook specialist stages")
    build.add_argument("workspace", type=Path)
    build.add_argument("--profile", type=Path)
    build.add_argument("--chapters", default="all")
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
            raise ValueError(f"engine option must use KEY=VALUE: {value!r}")
        result[key] = option_value
    return result


def _progress(event: BookProgressEvent) -> None:
    print(f"{event.stage}: {event.kind.removeprefix('stage.')}", file=sys.stderr)


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
    api.speak(
        " ".join(args.text),
        voice=args.voice,
        engine=args.engine,
        target=args.target,
        language=args.lang,
        speaker=args.speaker,
        offline=args.offline,
        refresh=args.refresh,
        engine_options=_engine_options(args.engine_option),
    )
    return 0


def _run_convert(args: argparse.Namespace) -> int:
    workspace = api.convert_book(
        args.source,
        args.output,
        chapters=args.chapters,
        language=args.lang,
    )
    result = {"workspace": str(workspace.path)}
    if args.json:
        print(json.dumps(result))
    else:
        print(f"Created .ssmdbook workspace: {workspace.path}")
    return 0


def _run_build(args: argparse.Namespace) -> int:
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
        output=args.output,
        export_profile=args.export_profile,
        cover=args.cover,
        bitrate=args.bitrate,
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


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "speak":
            return _run_speak(args)
        if args.command == "doctor":
            report = doctor_report()
            print(json.dumps(report, indent=2) if args.json else format_doctor_report(report))
            return 0
        if args.audiobook_command == "convert":
            return _run_convert(args)
        if args.audiobook_command == "build":
            return _run_build(args)
    except Exception as error:  # noqa: BLE001
        print(f"readio: {error}", file=sys.stderr)
        return 1
    raise AssertionError(f"unhandled command: {args.command}")


__all__ = ["build_parser", "main"]
