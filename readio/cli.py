"""CLI for native `.ssmd-bundle` workspace orchestration."""

from __future__ import annotations

import argparse
import json
import logging
import shlex
import sys
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import audiocompose  # type: ignore[import-not-found]
import utterplan  # type: ignore[import-not-found]

from . import __version__, api
from .cli_control import CLIUsageError, add_control_commands, run_control_command
from .diagnostics import configure_diagnostics, log_event, logger_for, timed_span
from .doctor import doctor_report, format_doctor_report
from .integrations.audioexport import supported_formats
from .intent import (
    CLIP_POLICIES,
    COMPOSE_EMPHASIS_POLICIES,
    DIAGNOSTICS_MODES,
    OVERLAP_MODES,
    PEAK_POLICIES,
    RENDERABILITY_MODES,
    SPACY_MODEL_SIZES,
    SPACY_MODES,
    TEXT_PREPARATIONS,
    _default_stem,
)
from .intent_resolver import resolve_render_intent
from .workspace import inspect_source_kind


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
        "intent_command",
        "intent_action",
        "bundle_command",
        "engine_command",
        "model_command",
        "voices_command",
        "voice_command",
        "profile_command",
        "role_command",
        "runtime_command",
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
    _add_intent_commands(commands)
    _add_render_command(commands)
    _add_status_command(commands)
    _add_bundle_commands(commands)
    _add_speak_command(commands)
    doctor = commands.add_parser("doctor", help="Report package and VoiceRender engine health")
    doctor.add_argument("--json", action="store_true")
    return parser


def _add_intent_commands(commands: Any) -> None:
    intent = commands.add_parser(
        "intent",
        help="Create, review, and validate declarative render intents",
        description="Create reviewable book and document render-intent TOML files.",
    )
    actions = intent.add_subparsers(dest="intent_command", required=True)

    for kind in ("book", "document"):
        kind_parser = actions.add_parser(kind, help=f"Manage {kind} render intents")
        kind_actions = kind_parser.add_subparsers(dest="intent_action", required=True)
        create = kind_actions.add_parser("create", help=f"Create a {kind} render intent")
        create.add_argument("source", type=Path)
        output_group = create.add_mutually_exclusive_group()
        output_group.add_argument("-o", "--output-config", type=Path)
        output_group.add_argument("--stdout", action="store_true")
        create.add_argument("--minimal", action="store_true")
        create.add_argument("--force", action="store_true", help="Replace an existing intent file")
        create.add_argument("--json", action="store_true")
        _add_intent_create_options(create, kind=kind)

    show = actions.add_parser("show", help="Review a saved render intent")
    show.add_argument("path", type=Path)
    show_modes = show.add_mutually_exclusive_group()
    show_modes.add_argument("--toml", action="store_true", help="Print normalized TOML")
    show_modes.add_argument(
        "--resolved", action="store_true", help="Resolve paths and inferred defaults"
    )
    show.add_argument("--json", action="store_true")

    validate = actions.add_parser("validate", help="Validate an intent without rendering")
    validate.add_argument("path", type=Path)
    validate.add_argument("--json", action="store_true")


def _add_intent_create_options(command: Any, *, kind: str) -> None:
    from .intent import ComposeIntent, PlanningIntent, RenderOptionsIntent

    command.add_argument("--profile", type=Path, required=True, help="VoiceRender RenderProfile")
    command.add_argument("--bundle", type=Path, help="Desired or known .ssmd-bundle path")
    command.add_argument("--variant", help="Optional UtterPlan projection label")
    command.add_argument("--language")
    if kind == "book":
        command.add_argument("--chapters", default="all")

    planning = PlanningIntent()
    command.add_argument("--diagnostics", choices=DIAGNOSTICS_MODES, default=planning.diagnostics)
    command.add_argument(
        "--text-preparation", choices=TEXT_PREPARATIONS, default=planning.text_preparation
    )
    command.add_argument("--overlap-mode", choices=OVERLAP_MODES, default=planning.overlap_mode)
    command.add_argument(
        "--renderability-mode", choices=RENDERABILITY_MODES, default=planning.renderability_mode
    )
    command.add_argument("--spacy", choices=SPACY_MODES, default=planning.spacy)
    command.add_argument("--spacy-model")
    command.add_argument("--spacy-model-size", choices=SPACY_MODEL_SIZES)
    command.add_argument(
        "--require-spacy",
        action=argparse.BooleanOptionalAction,
        default=planning.require_spacy,
    )

    render = RenderOptionsIntent()
    command.add_argument("--speech", default=render.speech)
    command.add_argument("--emphasis-policy", default=render.emphasis_policy)

    compose = ComposeIntent()
    command.add_argument(
        "--compose-emphasis-policy",
        choices=COMPOSE_EMPHASIS_POLICIES,
        default=compose.emphasis_policy,
    )
    command.add_argument("--compose-sample-rate", type=int, default=compose.sample_rate)
    command.add_argument("--target-lufs", type=float, default=compose.target_lufs)
    command.add_argument("--true-peak-ceiling-dbtp", type=float)
    command.add_argument("--clip-policy", choices=CLIP_POLICIES)
    command.add_argument("--peak-policy", choices=PEAK_POLICIES)

    if kind == "book":
        command.add_argument("--format", choices=("m4b",), default="m4b")
    else:
        command.add_argument("--format", default="mp3", help="AudioExport format (default: mp3)")
    command.add_argument("--output", type=Path, help="Final media output path")
    command.add_argument("--bitrate", help="AudioExport bitrate")
    command.add_argument("--export-sample-rate", type=int)
    command.add_argument("--channels", type=int)
    command.add_argument("--cover", type=Path)
    command.add_argument("--timeline", type=Path)

    for option in ("title", "author", "album", "year", "genre", "comment"):
        command.add_argument(f"--{option}")
    if kind == "book":
        command.add_argument("--narrator")
        command.add_argument("--series")
        command.add_argument("--series-index")
    command.add_argument(
        "--metadata",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Additional metadata; convenience flags take precedence",
    )


def _default_intent_config_path(source: Path) -> Path:
    return source.with_name(f"{_default_stem(source)}.readio.toml")


def _intent_metadata(args: argparse.Namespace, *, book: bool) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for entry in args.metadata:
        key, separator, value = entry.partition("=")
        key = key.strip()
        if not separator or not key:
            raise CLIUsageError(f"--metadata expects KEY=VALUE, got {entry!r}")
        if key in metadata:
            raise CLIUsageError(f"metadata key {key!r} was supplied more than once")
        metadata[key] = value

    convenience = ["title", "author", "album", "year", "genre", "comment"]
    if book:
        convenience.extend(("narrator", "series", "series_index"))
    for name in convenience:
        value = getattr(args, name, None)
        if value is not None:
            metadata[name] = value
    return metadata


def _create_render_intent(args: argparse.Namespace) -> int:
    if args.stdout and args.json:
        raise CLIUsageError("--stdout prints TOML and cannot be combined with --json")
    if args.stdout and args.force:
        raise CLIUsageError("--force applies only when writing an intent file")

    book = args.intent_command == "book"
    kind = "book" if book else "document"
    format_spec: Any | None = None
    output_format = args.format.casefold()
    if not book:
        formats = supported_formats()
        if output_format not in formats:
            supported = ", ".join(sorted(formats))
            raise CLIUsageError(
                f"AudioExport does not support output format {output_format!r}; "
                f"supported formats: {supported}"
            )
        format_spec = formats[output_format]

    kwargs: dict[str, Any] = {
        "profile": args.profile,
        "bundle": args.bundle,
        "language": args.language,
        "variant": args.variant,
        "diagnostics": args.diagnostics,
        "text_preparation": args.text_preparation,
        "overlap_mode": args.overlap_mode,
        "renderability_mode": args.renderability_mode,
        "spacy": args.spacy,
        "spacy_model": args.spacy_model,
        "spacy_model_size": args.spacy_model_size,
        "require_spacy": args.require_spacy,
        "speech": args.speech,
        "emphasis_policy": args.emphasis_policy,
        "compose_emphasis_policy": args.compose_emphasis_policy,
        "compose_sample_rate": args.compose_sample_rate,
        "target_lufs": args.target_lufs,
        "true_peak_ceiling_dbtp": args.true_peak_ceiling_dbtp,
        "clip_policy": args.clip_policy,
        "peak_policy": args.peak_policy,
        "output_format": output_format,
        "output": args.output,
        "bitrate": args.bitrate if args.bitrate is not None else ("96k" if book else None),
        "export_sample_rate": args.export_sample_rate,
        "channels": args.channels,
        "cover": args.cover,
        "timeline": args.timeline,
        "metadata": _intent_metadata(args, book=book),
    }
    if book:
        kwargs["chapters"] = args.chapters
        intent = api.create_book_intent(args.source, **kwargs)
    else:
        intent = api.create_document_intent(args.source, **kwargs)

    expected_extension = ".m4b" if book else getattr(format_spec, "extension", None)
    if not isinstance(expected_extension, str) or not expected_extension.startswith("."):
        raise CLIUsageError("AudioExport returned a format without a valid file extension")
    if (
        intent.output.path is None
        or intent.output.path.suffix.casefold() != expected_extension.casefold()
    ):
        raise CLIUsageError(
            f"output path extension must be {expected_extension} for format {output_format!r}"
        )
    if args.stdout:
        sys.stdout.write(api.dump_render_intent(intent, minimal=args.minimal))
        return 0

    config_path = args.output_config or _default_intent_config_path(args.source.expanduser())
    saved_path = api.save_render_intent(
        intent,
        config_path,
        minimal=args.minimal,
        force=args.force,
    )
    if args.json:
        return _emit_result(
            {
                "config": saved_path,
                "kind": kind,
                "source": intent.input.source,
                "bundle": intent.input.bundle,
                "profile": intent.voice.profile,
                "format": intent.output.format,
                "output": intent.output.path,
                "minimal": args.minimal,
            },
            as_json=True,
        )

    lines = [
        f"Created render intent: {saved_path}",
        "",
        f"Kind:       {kind}",
        f"Source:     {intent.input.source}",
    ]
    if book:
        lines.append(f"Selection:  {intent.input.chapters} chapters")
    lines.extend(
        (
            f"Profile:    {intent.voice.profile}",
            f"Format:     {intent.output.format}",
            f"Output:     {intent.output.path}",
            "",
            "Review:",
            f"  readio intent show {saved_path}",
            "",
            "Render:",
            f"  readio render --config {saved_path}",
        )
    )
    print("\n".join(lines))
    return 0


def _intent_display(value: object) -> str:
    return "<not set>" if value is None else str(value)


def _format_intent(intent: Any, path: Path) -> str:
    lines = [
        f"Intent: {path}",
        f"Schema: {intent.schema}",
        f"Kind:   {_intent_display(intent.kind)}",
        "",
        "Input",
        f"  source       {_intent_display(intent.input.source)}",
        f"  bundle       {_intent_display(intent.input.bundle)}",
    ]
    if intent.kind == "book":
        lines.append(f"  chapters     {_intent_display(intent.input.chapters)}")
    lines.extend(
        (
            f"  language     {_intent_display(intent.input.language)}",
            "",
            "Voice",
            f"  profile      {_intent_display(intent.voice.profile)}",
            f"  variant      {intent.voice.variant or '<automatic>'}",
            "",
            "Planning",
            f"  diagnostics          {intent.planning.diagnostics}",
            f"  text preparation     {intent.planning.text_preparation}",
            f"  overlap mode         {intent.planning.overlap_mode}",
            f"  renderability        {intent.planning.renderability_mode}",
            f"  spaCy                {intent.planning.spacy}",
            f"  spaCy model          {_intent_display(intent.planning.spacy_model)}",
            f"  spaCy model size     {_intent_display(intent.planning.spacy_model_size)}",
            f"  require spaCy        {intent.planning.require_spacy}",
            "",
            "Render",
            f"  speech               {intent.render.speech}",
            f"  render emphasis      {intent.render.emphasis_policy}",
            "",
            "Compose",
            f"  sample rate          {_intent_display(intent.compose.sample_rate)} Hz",
            f"  target loudness      {_intent_display(intent.compose.target_lufs)} LUFS",
            f"  emphasis policy      {intent.compose.emphasis_policy}",
            f"  true peak ceiling    {_intent_display(intent.compose.true_peak_ceiling_dbtp)} dBTP",
            f"  clip policy          {_intent_display(intent.compose.clip_policy)}",
            f"  peak policy          {_intent_display(intent.compose.peak_policy)}",
            "",
            "Output",
            f"  format               {intent.output.format}",
            f"  path                 {_intent_display(intent.output.path)}",
            f"  bitrate              {_intent_display(intent.output.bitrate)}",
            f"  sample rate          {_intent_display(intent.output.sample_rate)} Hz",
            f"  channels             {_intent_display(intent.output.channels)}",
            f"  cover                {_intent_display(intent.output.cover)}",
            f"  timeline             {_intent_display(intent.output.timeline)}",
        )
    )
    if intent.output.metadata:
        lines.append("  metadata")
        lines.extend(
            f"    {key} = {value}" for key, value in sorted(intent.output.metadata.items())
        )
    return "\n".join(lines)


def _format_resolved_intent(resolved: Any) -> str:
    return "\n".join(
        (
            f"Config path:    {resolved.config_path}",
            f"Kind:           {resolved.kind}",
            f"Source:         {resolved.source}",
            f"Bundle:         {resolved.bundle}",
            f"Profile:        {resolved.profile}",
            f"Chapters:       {resolved.chapters}",
            f"Format:         {resolved.output_format}",
            f"Output:         {resolved.output}",
        )
    )


def _run_intent_command(args: argparse.Namespace) -> int:
    if args.intent_command in {"book", "document"}:
        return _create_render_intent(args)

    intent = api.load_render_intent(args.path)
    if args.intent_command == "show":
        if args.toml and args.json:
            raise CLIUsageError("--toml cannot be combined with --json")
        if args.toml:
            sys.stdout.write(api.dump_render_intent(intent))
            return 0
        if args.resolved:
            resolved = resolve_render_intent(
                intent,
                config_path=args.path,
                check_output_collision=False,
            )
            return _emit_result(
                {"intent": intent, "resolved": resolved},
                as_json=args.json,
                human=_format_resolved_intent(resolved),
            )
        return _emit_result(
            intent,
            as_json=args.json,
            human=_format_intent(intent, args.path),
        )

    if args.intent_command == "validate":
        resolved = resolve_render_intent(
            intent,
            config_path=args.path,
            check_output_collision=False,
        )
        return _emit_result(
            {"valid": True, "config": args.path, "resolved": resolved},
            as_json=args.json,
            human=f"Intent is valid: {args.path}",
        )
    raise AssertionError(f"unknown intent command {args.intent_command}")


def _render_execution_options(args: argparse.Namespace) -> api.RenderExecutionOptions:
    stage_names: dict[str, api.PipelineStage] = {
        "convert": "convert",
        "compile": "compile",
        "render-plan": "render_plan",
        "render": "render",
        "compose": "compose",
        "export": "export",
    }
    requested = list(args.force_stage)
    if args.reconvert and "convert" not in requested:
        requested.append("convert")
    force_stages = tuple(dict.fromkeys(stage_names[name] for name in requested))
    restart_from = stage_names[args.restart_from] if args.restart_from is not None else None
    return api.RenderExecutionOptions(
        force_stages=force_stages,
        restart_from=restart_from,
        reset_stale_output=args.reset_stale_output,
        ffmpeg=args.ffmpeg,
        ffprobe=args.ffprobe,
    )


def _one_off_render_intent(args: argparse.Namespace, service: api.Readio) -> api.RenderIntent:
    if args.source is None:
        raise CLIUsageError("provide SOURCE or --config PATH")
    if args.profile is None:
        raise CLIUsageError("--profile is required when no --config is supplied")

    if args.source.name.casefold().endswith(".ssmd-bundle"):
        kind = service.resolve_workspace(args.source).kind
    else:
        kind = inspect_source_kind(args.source)
    kwargs: dict[str, Any] = {
        "profile": args.profile,
        "bundle": args.bundle,
        "language": args.language,
        "variant": args.variant,
        "output_format": args.output_format or "auto",
        "output": args.output,
    }
    if kind == "book":
        kwargs["chapters"] = args.chapters or "all"
        return api.create_book_intent(args.source, **kwargs)
    return api.create_document_intent(args.source, **kwargs)


def _format_render_plan(plan: api.RenderIntentPlan) -> str:
    resolved = plan.resolved
    lines = [
        "Readio render (dry run)",
        f"Source:   {resolved.source}",
        f"Kind:     {resolved.kind}",
        f"Bundle:   {resolved.bundle}",
        f"Profile:  {resolved.profile}",
        f"Output:   {resolved.output}",
        "",
    ]
    status = plan.status
    variant_status = None
    if status is not None:
        candidates = [item for item in status.variants if item.profile == resolved.profile]
        if resolved.intent.voice.variant is not None:
            variant_status = next(
                (item for item in candidates if item.variant == resolved.intent.voice.variant),
                None,
            )
        if variant_status is None and candidates:
            variant_status = candidates[0]

    stage_states = {
        "convert": "required" if plan.convert_required else "reuse",
        "compile": status.compile.state if status is not None else "not_started",
        "render-plan": (
            variant_status.render_plan.state if variant_status is not None else "not_started"
        ),
        "render": variant_status.render.state if variant_status is not None else "not_started",
        "compose": variant_status.compose.state if variant_status is not None else "not_started",
        "export": variant_status.export.state if variant_status is not None else "not_started",
    }
    for index, stage in enumerate(plan.stages, start=1):
        detail = ""
        if stage == "render-plan" and variant_status is not None:
            detail = f" variant={variant_status.variant}"
        lines.append(f"[{index}/{len(plan.stages)}] {stage:<12} {stage_states[stage]}{detail}")
    if plan.output_exists:
        lines.extend(("", "Output already exists; use --force-stage export to replace it."))
    return "\n".join(lines)


def _run_render_command(args: argparse.Namespace, service: api.Readio) -> int:
    intent = (
        api.load_render_intent(args.config)
        if args.config is not None
        else _one_off_render_intent(args, service)
    )
    overrides = {
        "config_path": args.config,
        "source": args.source,
        "bundle": args.bundle,
        "profile": args.profile,
        "variant": args.variant,
        "chapters": args.chapters,
        "language": args.language,
        "output_format": args.output_format,
        "output": args.output,
    }
    overrides = {name: value for name, value in overrides.items() if value is not None}
    options = _render_execution_options(args)
    if args.dry_run:
        plan = service.plan_render_intent(intent, options, **overrides)
        return _emit_result(
            plan,
            as_json=args.json,
            human=_format_render_plan(plan),
        )

    result = service.render_intent(
        intent,
        options,
        on_progress=None if args.no_progress else _progress,
        **overrides,
    )
    human = "\n".join(
        (
            "Readio render complete",
            f"Bundle:  {result.workspace.root}",
            f"Kind:    {result.kind}",
            f"Profile: {result.profile}",
            f"Variant: {result.variant}",
            f"Output:  {result.output}",
        )
    )
    return _emit_result(result, as_json=args.json, human=human)


def _run_intent_status(args: argparse.Namespace, service: api.Readio) -> int:
    intent = api.load_render_intent(args.config)
    resolved = service.resolve_intent(
        intent,
        config_path=args.config,
        check_output_collision=False,
    )
    status = None
    if resolved.bundle.exists():
        status = service.status(
            resolved.bundle,
            api.StatusRequest(
                chapters=resolved.chapters,
                profile=resolved.profile,
                variant=resolved.intent.voice.variant,
            ),
        )
    next_action = status.next_actions[0] if status is not None and status.next_actions else None
    result = {
        "resolved": resolved,
        "status": status,
        "next_action": next_action,
        "convert_required": status is None,
    }
    lines = [
        f"Source:   {resolved.source}",
        f"Bundle:   {resolved.bundle}",
        f"Profile:  {resolved.profile}",
        f"Output:   {resolved.output}",
        "",
    ]
    if status is None:
        lines.extend(
            ("convert       not_started", "compile       not_started", "Next:", "  convert")
        )
    else:
        lines.append(_format_bundle_status(status, resolved.chapters))
        if next_action is not None:
            lines.extend(("", "Next:", f"  {next_action.stage}"))
    return _emit_result(result, as_json=args.json, human="\n".join(lines))


def _add_status_command(commands: Any) -> None:
    status = commands.add_parser("status", help="Inspect status using a saved render intent")
    status.add_argument(
        "--config", type=Path, required=True, help="Saved *.readio.toml render intent"
    )
    status.add_argument("--json", action="store_true")


def _add_render_command(commands: Any) -> None:
    command = commands.add_parser(
        "render",
        help="Render a source or saved intent through all specialist stages",
        description="Converge source material and a RenderProfile to final audio output.",
    )
    command.add_argument("source", nargs="?", type=Path)
    command.add_argument("--config", type=Path, help="Saved *.readio.toml render intent")
    command.add_argument(
        "--profile", type=Path, help="Override or supply a VoiceRender RenderProfile"
    )
    command.add_argument("--bundle", type=Path, help="Override the target .ssmd-bundle path")
    command.add_argument("--variant", help="Override the user-friendly UtterPlan variant label")
    command.add_argument("--chapters", help="Override the book chapter selector")
    command.add_argument("--language", help="Override source/planner language")
    command.add_argument("--format", dest="output_format", help="Override final AudioExport format")
    command.add_argument("--output", type=Path, help="Override final media output path")
    command.add_argument(
        "--dry-run", action="store_true", help="Resolve and inspect status without rendering"
    )
    command.add_argument(
        "--force-stage",
        action="append",
        choices=("convert", "compile", "render-plan", "render", "compose", "export"),
        default=[],
        help="Explicitly force one stage; may be repeated",
    )
    command.add_argument(
        "--restart-from",
        choices=("convert", "compile", "render-plan", "render", "compose", "export"),
        help="Force this stage and every later stage",
    )
    command.add_argument("--reconvert", action="store_true", help="Explicitly force conversion")
    command.add_argument("--reset-stale-output", action="store_true")
    command.add_argument("--ffmpeg", type=Path, help="FFmpeg executable override")
    command.add_argument("--ffprobe", type=Path, help="FFprobe executable override")
    command.add_argument("--json", action="store_true")
    command.add_argument("--no-progress", action="store_true")


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
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
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
        for name in ("package", "workspace", "profile", "variant", "next_action"):
            value = getattr(error, name, None)
            if value is not None:
                details[name] = str(value) if isinstance(value, Path) else value
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
            if args.command == "intent":
                return _run_intent_command(args)
            service = api.Readio()
            if args.command == "render":
                return _run_render_command(args, service)
            if args.command == "status":
                return _run_intent_status(args, service)
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
            if args.command in {"engines", "models", "voices", "voice", "profile", "runtime"}:
                return run_control_command(args, api)
    except CLIUsageError as error:
        return _report_error(error, args, status=2)
    except Exception as error:  # noqa: BLE001
        if verbosity >= 2:
            traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)
        return _report_error(error, args, status=1)
    raise AssertionError(f"unhandled command: {args.command}")


__all__ = ["build_parser", "main"]
