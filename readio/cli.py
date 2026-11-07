"""CLI for unified Readio speech and audio workflow orchestration."""

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
from .render_service import _compose_options, _planner_config
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
        "engine_command",
        "model_command",
        "voices_command",
        "voice_command",
        "profile_command",
        "profile_document_command",
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
        description="Orchestrate speech and audio workflows with specialist-backed tools",
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

    command.add_argument("--profile", type=Path, help="VoiceRender RenderProfile")
    command.add_argument("--bundle", type=Path, help="Persistent bundle workspace; omitted selects temporary mode")
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


class _ProfileRequiredError(CLIUsageError):
    code = "profile_required"

    def __init__(
        self,
        message: str,
        *,
        source_roles: list[str],
        next_action: list[str],
    ) -> None:
        self.source_roles = source_roles
        self.next_action = next_action
        super().__init__(message)


def _profile_required_error(args: argparse.Namespace, kind: str) -> _ProfileRequiredError:
    source = args.source.expanduser()
    source_roles: list[str] = []
    if kind == "document" and source.name.casefold().endswith(".ssmd.md") and source.is_file():
        requirements = api.inspect_document_voice_requirements(source)
        source_roles = [role.name for role in requirements.roles]

    suffix = ".ssmd.md"
    stem = source.name[: -len(suffix)] if source.name.casefold().endswith(suffix) else source.stem
    profile_output = source.with_name(f"{stem}.renderprofile.toml")
    if source_roles:
        next_action = [
            "readio",
            "profile",
            "document",
            "create",
            str(source),
            "--engine",
            "<ENGINE>",
            "--target",
            "<TARGET>",
            "--language",
            "<LANGUAGE>",
        ]
        for role in source_roles:
            next_action.extend(("--voice", f"{role}=<VOICE>"))
    else:
        next_action = [
            "readio",
            "profile",
            "create",
            "--engine",
            "<ENGINE>",
            "--target",
            "<TARGET>",
            "--language",
            "<LANGUAGE>",
            "--voice",
            "<VOICE>",
        ]
    next_action.extend(("-o", str(profile_output)))

    lines = ["this render intent needs a VoiceRender profile."]
    if source_roles:
        lines.extend(
            ["", "Detected document voice roles:", *(f"  {role}" for role in source_roles)]
        )
    lines.extend(
        [
            "",
            "Create one first:",
            f"  {shlex.join(next_action)}",
            "",
            "Then:",
            f"  {shlex.join(['readio', 'intent', kind, 'create', str(source), '--profile', str(profile_output)])}",
        ]
    )
    return _ProfileRequiredError(
        "\n".join(lines),
        source_roles=source_roles,
        next_action=next_action,
    )


def _create_render_intent(args: argparse.Namespace) -> int:
    if args.stdout and args.json:
        raise CLIUsageError("--stdout prints TOML and cannot be combined with --json")
    if args.stdout and args.force:
        raise CLIUsageError("--force applies only when writing an intent file")

    book = args.intent_command == "book"
    kind = "book" if book else "document"

    if args.profile is None:
        raise _profile_required_error(args, kind)
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
                "workspace": {"mode": intent.workspace.mode, "bundle": intent.workspace.bundle},
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
    ]
    if intent.kind == "book":
        lines.append(f"  chapters     {_intent_display(intent.input.chapters)}")
    lines.extend(
        (
            f"  language     {_intent_display(intent.input.language)}",
            "",
            "Workspace",
            f"  mode         {intent.workspace.mode}",
            f"  bundle       {_intent_display(intent.workspace.bundle)}",
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
            f"Workspace:      {resolved.workspace_mode}",
            f"Bundle:         {resolved.bundle or '<temporary>'}",
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


def _render_request(args: argparse.Namespace, service: api.Readio) -> api.RenderRequest:
    intent = (
        api.load_render_intent(args.config)
        if getattr(args, "config", None) is not None
        else _one_off_render_intent(args, service)
    )
    resolved = resolve_render_intent(
        intent,
        config_path=getattr(args, "config", None),
        source=getattr(args, "source", None),
        bundle=getattr(args, "bundle", None),
        profile=getattr(args, "profile", None),
        variant=getattr(args, "variant", None),
        chapters=getattr(args, "chapters", None),
        language=getattr(args, "language", None),
        output_format=getattr(args, "output_format", None),
        output=getattr(args, "output", None),
        check_output_collision=False,
    )
    rebuild_from = getattr(args, "rebuild_from", None)
    if rebuild_from is not None:
        rebuild_from = rebuild_from.replace("-", "_")
    composition = api.CompositionOptions(
        options=_compose_options(resolved),
        emphasis_policy=resolved.intent.compose.emphasis_policy,
    )
    export = api.ExportOptions(
        metadata=resolved.intent.output.metadata,
        cover=resolved.intent.output.cover,
        timeline=resolved.intent.output.timeline,
        ffmpeg=getattr(args, "ffmpeg", None),
        ffprobe=getattr(args, "ffprobe", None),
    )
    return api.RenderRequest(
        source=resolved.source,
        profile=resolved.profile,
        chapters=resolved.chapters,
        language=resolved.language,
        bundle=resolved.bundle,
        variant=resolved.intent.voice.variant,
        output=resolved.output,
        format=resolved.output_format,
        bitrate=resolved.intent.output.bitrate,
        sample_rate=resolved.intent.output.sample_rate,
        channels=resolved.intent.output.channels,
        rebuild_from=rebuild_from,
        replace_output=bool(getattr(args, "replace_output", False)),
        planning=api.PlanningOptions(
            planner_config=_planner_config(resolved),
            diagnostics=resolved.intent.planning.diagnostics,
        ),
        voice_render=api.VoiceRenderOptions(
            speech=resolved.intent.render.speech,
            emphasis_policy=resolved.intent.render.emphasis_policy,
        ),
        composition=composition,
        export=export,
    )


def _format_render_plan(plan: api.WorkflowPlan) -> str:
    workspace = str(plan.workspace) if plan.workspace is not None else "<temporary>"
    lines = [
        "Readio workflow inspection",
        f"Source:         {plan.source}",
        f"Workspace mode: {plan.workspace_mode}",
        f"Workspace:      {workspace}",
        f"Variant:        {plan.variant or '<automatic>'}",
        f"Output:         {plan.output or '<none>'}",
        "",
    ]
    if plan.steps:
        lines.append("Workflow")
        for index, step in enumerate(plan.steps, start=1):
            stage = step.stage.replace("_", "-")
            selector = f" [{step.selector}]" if step.selector else ""
            lines.append(
                f"[{index}/{len(plan.steps)}] {stage:<12} {step.action:<8} "
                f"{step.state:<12}{selector}"
            )
            if step.reason:
                lines.append(f"    {step.reason}")
    if plan.warnings:
        lines.extend(("", "Warnings"))
        lines.extend(f"  - {warning}" for warning in plan.warnings)
    return "\n".join(lines)


def _run_render_command(args: argparse.Namespace, service: api.Readio) -> int:
    request = _render_request(args, service)
    if args.dry_run:
        plan = service.inspect(request)
        return _emit_result(plan, as_json=args.json, human=_format_render_plan(plan))

    result = service.render(
        request,
        on_progress=None if args.no_progress else _progress,
    )
    workspace = str(result.workspace) if result.workspace is not None else "<temporary>"
    human = "\n".join(
        (
            "Readio render complete",
            f"Workspace mode: {result.workspace_mode}",
            f"Workspace:      {workspace}",
            f"Variant:        {result.variant}",
            f"Output:         {result.output}",
        )
    )
    return _emit_result(result, as_json=args.json, human=human)


def _run_intent_status(args: argparse.Namespace, service: api.Readio) -> int:
    request = _render_request(args, service)
    plan = service.inspect(request)
    return _emit_result(plan, as_json=args.json, human=_format_render_plan(plan))


def _add_status_command(commands: Any) -> None:
    status = commands.add_parser("status", help="Inspect the unified render workflow")
    status.add_argument("source", nargs="?", type=Path)
    status.add_argument("--config", type=Path, help="Saved *.readio.toml render intent")
    status.add_argument("--profile", type=Path)
    status.add_argument("--bundle", type=Path)
    status.add_argument("--variant")
    status.add_argument("--chapters")
    status.add_argument("--language")
    status.add_argument("--format", dest="output_format")
    status.add_argument("--output", type=Path)
    status.add_argument("--json", action="store_true")

def _add_render_command(commands: Any) -> None:
    command = commands.add_parser(
        "render",
        help="Render a source or saved intent to final audio output",
        description="Converge source material and a RenderProfile to final audio output.",
    )
    command.add_argument("source", nargs="?", type=Path)
    command.add_argument("--config", type=Path, help="Saved *.readio.toml render intent")
    command.add_argument(
        "--profile", type=Path, help="Override or supply a VoiceRender RenderProfile"
    )
    command.add_argument("--bundle", type=Path, help="Use a persistent bundle workspace; omitted means temporary")
    command.add_argument("--variant", help="Override the user-friendly UtterPlan variant label")
    command.add_argument("--chapters", help="Override the book chapter selector")
    command.add_argument("--language", help="Override source/planner language")
    command.add_argument("--format", dest="output_format", help="Override final AudioExport format")
    command.add_argument("--output", type=Path, help="Override final media output path")
    command.add_argument(
        "--dry-run", action="store_true", help="Resolve and inspect status without rendering"
    )
    command.add_argument(
        "--replace-output",
        action="store_true",
        help="Explicitly allow replacement of an existing final media output",
    )
    command.add_argument(
        "--rebuild-from",
        choices=("convert", "compile", "render-plan", "render", "compose", "export"),
        help="Rebuild this workflow stage and downstream derived state",
    )
    command.add_argument("--ffmpeg", type=Path, help="FFmpeg executable override")
    command.add_argument("--ffprobe", type=Path, help="FFprobe executable override")
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




def _progress(event: api.WorkflowEvent) -> None:
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
        for name in ("package", "workspace", "profile", "variant", "source_roles", "next_action"):
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
