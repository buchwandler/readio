from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from . import __version__, cli_adapter
from . import api as public_api
from .cli_help import ReadioArgumentParser, show_help
from .cli_present import format_plan_human
from .jsonutil import json_value as _json_value
from .logging_config import MAX_VERBOSITY, configure_logging
from .progress import TerminalProgress

logger = logging.getLogger(__name__)


def _add_input_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "text",
        nargs="*",
        help="literal text, or one existing file path; omit to read stdin",
    )
    parser.add_argument(
        "--file",
        type=Path,
        help="unambiguous scripting form; read or convert a document file",
    )
    parser.add_argument(
        "--input-format",
        choices=("auto", "text", "markdown", "ssmd"),
        default="auto",
        help=(
            "auto uses ssmdconvert for file inputs; explicit text/markdown/ssmd "
            "selects the input format; text disables positional file detection"
        ),
    )
    parser.add_argument(
        "--select",
        default="all",
        metavar="SELECTOR",
        help="all (default), last-paragraph, or paragraph:N",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="consume stdin incrementally and start each paragraph when a blank line closes it",
    )


def _add_audio_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        choices=public_api.SUPPORTED_AUDIO_FORMATS,
        help=("audio output format; inferred from --output when possible; default: wav"),
    )


MASTERING_PROFILE_CHOICES = (
    "spoken-word",
    "spoken-word-dual-mono",
    "broadcast-ebu",
    "peak-safe",
    "off",
)


def _add_mastering_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--mastering",
        choices=MASTERING_PROFILE_CHOICES,
        default="spoken-word",
        help="mastering profile (default: spoken-word; omitted numeric overrides inherit)",
    )
    parser.add_argument(
        "--target-lufs",
        type=float,
        help="expert LUFS target override; omitted means inherit from --mastering",
    )
    parser.add_argument(
        "--true-peak-ceiling-dbtp",
        type=float,
        help="expert true-peak ceiling override; omitted means inherit from --mastering",
    )
    parser.add_argument("--peak-policy", choices=("reduce_gain", "error"), default="reduce_gain")
    parser.add_argument("--clip-policy", choices=("clamp", "warn", "error"), default="clamp")
    parser.add_argument("--sample-rate", type=int, help="AudioCompose output sample rate")


def _add_synthesis_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--engine",
        help="synthesis engine; default from configuration",
    )
    parser.add_argument("--offline", action="store_true", help="do not fetch engine assets")
    parser.add_argument("--refresh", action="store_true", help="refresh engine discovery metadata")
    voice_group = parser.add_mutually_exclusive_group()
    voice_group.add_argument(
        "--voice",
        help="semantic voice reference or native voice ID, e.g. kokoro:v1.0/af_heart, piper:en_US-amy-medium, or pocket:english_2026-04/alba",
    )
    parser.add_argument("--speaker", help="named or numeric speaker for multi-speaker engines")
    voice_group.add_argument(
        "--voice-file",
        type=Path,
        help="PocketSynth reference voice WAV (use instead of --voice or --voice-prompt)",
    )
    voice_group.add_argument(
        "--voice-prompt",
        help="PocketSynth managed reference prompt, for example kyutai-tts-voices:alba-mackenna/casual; use instead of --voice or --voice-file",
    )
    parser.add_argument("--lang", help="language code, e.g. en-us, de, fr")
    parser.add_argument("--model", help="runtime model ID")
    parser.add_argument(
        "--model-source",
        choices=("github", "huggingface"),
        help="distribution source for model discovery and runtime",
    )
    parser.add_argument("--quality", help="model quality/quantization")
    lexicon_group = parser.add_mutually_exclusive_group()
    lexicon_group.add_argument(
        "--lexicon",
        dest="lexicons",
        action="append",
        metavar="NAME",
        help="named synthesis lexicon, e.g. crane; repeat for layered lookup",
    )
    lexicon_group.add_argument(
        "--no-lexicons",
        action="store_true",
        help="explicitly disable static lexicon layers (provider-only)",
    )
    lexicon_group.add_argument(
        "--auto-lexicons",
        action="store_true",
        help="use automatic language-default lexicons",
    )
    parser.add_argument("--g2p-fallback", choices=public_api.G2P_FALLBACKS)
    parser.add_argument("--lexicon-data-policy", choices=public_api.LEXICON_DATA_POLICIES)
    parser.add_argument(
        "--spacy",
        choices=public_api.SPACY_POLICIES,
        help=(
            "spaCy model policy: auto selects the largest installed compatible model "
            "and falls back when unavailable; off disables spaCy; sm/md/lg/trf "
            "require that exact model tier"
        ),
    )
    parser.add_argument(
        "--short-sentence",
        choices=public_api.SHORT_SENTENCE_POLICIES,
        help=(
            "short-sentence synthesis strategy (default: phrase): off disables handling; "
            "wrap uses lightweight phoneme context; phrase uses carrier-phrase extraction; "
            "randomized-phrase uses randomized carrier-phrase extraction"
        ),
    )
    parser.add_argument("--language-detection", choices=public_api.LANGUAGE_DETECTION_MODES)
    parser.add_argument(
        "--detect-language", dest="detect_languages", action="append", metavar="LANG"
    )
    parser.add_argument(
        "--allow-experimental", action="store_true", help="allow experimental frontends"
    )
    parser.add_argument("--speed", type=float, help="speech speed multiplier")
    parser.add_argument(
        "--voice-level",
        choices=public_api.VOICE_LEVEL_MODES,
        help="static identity-specific voice gain calibration (default: off)",
    )
    parser.add_argument(
        "--precision",
        choices=("int8", "fp32"),
        help="PocketSynth bundle precision",
    )
    parser.add_argument("--temperature", type=float, help="PocketSynth generation temperature")
    parser.add_argument("--lsd-steps", type=int, help="PocketSynth latent diffusion steps")
    parser.add_argument("--max-frames", type=int, help="PocketSynth maximum generated frames")
    parser.add_argument(
        "--frames-after-eos",
        type=int,
        help="PocketSynth frames generated after end of sequence",
    )
    parser.add_argument(
        "--pause-mode",
        choices=("tts", "manual", "auto"),
        help=(
            "pause handling mode; auto is the Readio default, "
            "tts leaves pause timing to the TTS model, "
            "manual uses explicit boundary pauses"
        ),
    )
    parser.add_argument("--unit", choices=("sentence", "paragraph"))


def _add_voice_resolution_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--voice-bind",
        action="append",
        default=[],
        metavar="ROLE=VOICE_ID",
        help="bind one missing SSMD role for this invocation; repeatable",
    )
    parser.add_argument(
        "--resolve-voices",
        action="store_true",
        help="interactively choose missing SSMD voices when attached to a TTY",
    )


def _parse_voice_bindings(values: Sequence[str]) -> dict[str, str]:
    return cli_adapter.parse_voice_bindings(values)


def _add_runtime_options(parser: argparse.ArgumentParser, *, playback: bool = True) -> None:
    _add_synthesis_options(parser)
    _add_voice_resolution_options(parser)
    if playback:
        parser.add_argument("--queue-size", type=int, help="audio queue depth")
        parser.add_argument("--device", help="sounddevice output device name or id")


def _add_progress_option(
    parser: argparse.ArgumentParser,
    *,
    default: object = None,
) -> None:
    parser.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=default,
        help=(
            "show progress on stderr; enabled automatically on an "
            "interactive terminal, use --no-progress to disable"
        ),
    )


@dataclass(frozen=True, slots=True)
class GlobalCliOptions:
    json: bool = False
    verbosity: int = 0


def _extract_global_options(
    argv: Sequence[str] | None,
) -> tuple[list[str] | None, GlobalCliOptions]:
    if argv is None:
        return None, GlobalCliOptions()

    extracted: list[str] = []
    json_enabled = False
    verbosity = 0
    literal = False
    for value in argv:
        if literal:
            extracted.append(value)
        elif value == "--":
            literal = True
            extracted.append(value)
        elif value == "--json":
            json_enabled = True
        elif value in ("-v", "--verbose"):
            verbosity += 1
        elif value.startswith("-") and len(value) > 2 and set(value[1:]) == {"v"}:
            verbosity += len(value) - 1
        else:
            extracted.append(value)
    return extracted, GlobalCliOptions(
        json=json_enabled,
        verbosity=min(verbosity, MAX_VERBOSITY),
    )


def progress_enabled(args: argparse.Namespace, stream: object = sys.stderr) -> bool:
    explicit = getattr(args, "progress", None)
    if explicit is not None:
        return explicit
    if getattr(args, "json", False):
        return False
    return bool(stream.isatty())  # type: ignore[attr-defined]


def _build_progress(args: argparse.Namespace) -> TerminalProgress:
    stream = sys.stderr
    tty = bool(stream.isatty()) and getattr(args, "verbose", 0) == 0
    return TerminalProgress(
        stream=stream,
        enabled=progress_enabled(args, stream),
        tty=tty,
    )


def _progress_source_label(args: argparse.Namespace) -> str:
    if getattr(args, "file", None) is not None:
        return str(args.file)
    return "live input" if getattr(args, "live", False) else "input"


def _resolved_config(args: argparse.Namespace) -> public_api.ReadioConfig:
    config = public_api.Readio().config
    updates = {
        name: value
        for name in ("queue_size", "device")
        if (value := getattr(args, name, None)) is not None
    }
    if not updates:
        return config
    return replace(config, reader=replace(config.reader, **updates))


def _api_for(args: argparse.Namespace | None = None) -> public_api.Readio:
    if args is None:
        return public_api.Readio()
    return public_api.Readio(config=_resolved_config(args))


def _api_progress_handler(progress: TerminalProgress) -> public_api.EventHandler | None:
    return cli_adapter.public_event_handler(progress)


def _normalize_positional_input(args: argparse.Namespace) -> None:
    cli_adapter.normalize_positional_input(args)


def _read_input(args: argparse.Namespace, cfg: public_api.ReadioConfig) -> public_api.Document:
    return cli_adapter.read_document(args)


def _validate_live(args: argparse.Namespace) -> None:
    if args.file is not None or args.text:
        raise ValueError("--live reads stdin only; do not combine it with text or --file")
    if args.input_format in ("markdown", "ssmd"):
        raise ValueError(
            f"--live supports plain text only; {args.input_format.upper()} requires complete-document parsing"
        )
    if args.select != "all":
        raise ValueError("--select is not available with --live")
    if sys.stdin.isatty():
        raise ValueError("--live requires piped stdin")


def _prompt_for_missing_voices(
    app: public_api.Readio,
    result: public_api.SSMDAnalysis,
    synthesis: public_api.SynthesisRequest | None = None,
) -> dict[str, str]:
    return cli_adapter.prompt_missing_voices(result, app, synthesis)


def _cmd_speak(args: argparse.Namespace) -> int:
    app = _api_for(args)
    if args.live:
        _normalize_positional_input(args)
        _validate_live(args)
        app.speech.speak_live(
            sys.stdin,
            synthesis=_synthesis_request_from_args(args, default_language=app.config.reader.lang),
            unit=args.unit,
        )
        return 0

    request = _build_plan_request(args, app, operation="speak", allow_interactive=True)
    progress = _build_progress(args)
    with progress:
        app.speech.speak(request, on_event=_api_progress_handler(progress))
    return 0


def _synthesis_request_from_args(
    args: argparse.Namespace,
    *,
    default_language: str | None = None,
) -> public_api.SynthesisRequest:
    return cli_adapter.synthesis_request_from_args(args, default_language=default_language)


def _build_plan_request(
    args: argparse.Namespace,
    app: public_api.Readio,
    *,
    operation: Literal["speak", "render"] = "render",
    allow_interactive: bool = False,
) -> public_api.PlanRequest:
    """Build a PlanRequest from CLI args.

    ``allow_interactive`` permits ``--resolve-voices`` prompting before the
    request is constructed (normal render only).  ``readio plan`` and
    ``render --dry-run`` stay deterministic and reject the flag.
    """
    cfg = app.config
    _normalize_positional_input(args)
    document = _read_input(args, cfg)
    bindings = _parse_voice_bindings(getattr(args, "voice_bind", []))

    if getattr(args, "resolve_voices", False):
        if not allow_interactive:
            raise ValueError(
                "--resolve-voices is not available during plan/dry-run; "
                "use --voice-bind ROLE=VOICE_ID or persistent role configuration"
            )
        if getattr(args, "json", False) or not sys.stdin.isatty():
            raise ValueError(
                "--resolve-voices requires an interactive terminal; "
                "provide --voice-bind ROLE=VOICE_ID instead"
            )
        result = app.ssmd.analyze(document, bindings=bindings or None)
        if result.unresolved_references:
            bindings.update(_prompt_for_missing_voices(app, result))

    synthesis = _synthesis_request_from_args(args)

    output = public_api.OutputRequest(
        mode="file" if operation == "render" else "playback",
        requested_format=getattr(args, "format", None),
        requested_path=getattr(args, "output", None),
        force=bool(getattr(args, "force", False)),
    )

    return cli_adapter.build_plan_request(
        args,
        document=document,
        synthesis=synthesis,
        output=output,
        voice_bindings=bindings,
        operation=operation,
    )


def _project_synthesis_request(args: argparse.Namespace) -> public_api.SynthesisRequest:
    config = _resolved_config(args)
    return _synthesis_request_from_args(args, default_language=config.reader.lang)


def _project_build_request(args: argparse.Namespace) -> public_api.ProjectBuildRequest:
    return public_api.ProjectBuildRequest(
        target="export",
        selection=getattr(args, "select", "all"),
        voice_bindings=_parse_voice_bindings(getattr(args, "voice_bind", [])),
        synthesis=_project_synthesis_request(args),
        composition=public_api.CompositionOptions(
            mastering=getattr(args, "mastering", "spoken-word"),
            target_lufs=getattr(args, "target_lufs", None),
            true_peak_ceiling_dbtp=getattr(args, "true_peak_ceiling_dbtp", None),
            peak_policy=getattr(args, "peak_policy", "reduce_gain"),
            clip_policy=getattr(args, "clip_policy", "clamp"),
            sample_rate=getattr(args, "sample_rate", None),
        ),
        export=public_api.ExportOptions(format=getattr(args, "format", None) or "wav"),
    )


def _cmd_synth(args: argparse.Namespace) -> int:
    app = _api_for(args)
    project = args.project or Path.cwd()
    progress = _build_progress(args)
    with progress:
        result = app.projects.synthesize(
            project,
            _project_synthesis_request(args),
            selection=args.select,
            voice_bindings=_parse_voice_bindings(getattr(args, "voice_bind", [])),
            activate=True,
            on_event=_api_progress_handler(progress),
        )
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, **result.to_dict()}, ensure_ascii=False))
    else:
        print(f"Synthesis profile: {result.profile_id}")
        print(f"Synthesis cache: {result.reused} reused, {result.rendered} rendered")
    return 0


def _metric_pair(before: float | None, after: float | None, unit: str) -> str:
    left = "n/a" if before is None else f"{before:.2f}"
    right = "n/a" if after is None else f"{after:.2f}"
    return f"{left} → {right} {unit}"


def _print_loudness_summary(summary: public_api.LoudnessSummary) -> None:
    print(f"Mastering profile: {summary.profile}")
    if summary.target_lufs is None:
        target_status = "no LUFS target"
        target = "none"
    else:
        target = f"{summary.target_lufs:.2f} LUFS"
        if summary.target_reached:
            target_status = "target reached"
        elif summary.integrated_lufs_before is None:
            target_status = "target not measurable"
        else:
            target_status = "target not reached"
    print(
        f"Integrated loudness: "
        f"{_metric_pair(summary.integrated_lufs_before, summary.integrated_lufs_after, 'LUFS')} "
        f"(target {target}; {target_status})"
    )
    ceiling = (
        "none"
        if summary.true_peak_ceiling_dbtp is None
        else f"{summary.true_peak_ceiling_dbtp:.2f} dBTP"
    )
    print(
        f"True peak: "
        f"{_metric_pair(summary.true_peak_dbtp_before, summary.true_peak_dbtp_after, 'dBTP')} "
        f"(ceiling {ceiling})"
    )
    print(
        f"Sample peak: "
        f"{_metric_pair(summary.sample_peak_dbfs_before, summary.sample_peak_dbfs_after, 'dBFS')}"
    )
    print(
        f"Gain: applied {summary.applied_gain_db:+.2f} dB "
        f"(requested {summary.requested_gain_db:+.2f} dB)"
    )
    print(
        "Finalization timing: "
        f"analysis {summary.analysis_seconds:.3f}s, "
        f"gain {summary.gain_seconds:.3f}s, "
        f"post-gain metrics {summary.post_gain_metrics_seconds:.3f}s"
    )
    if summary.warning:
        print(f"Mastering warning: {summary.warning}")


def _print_project_build_result(result: public_api.ProjectBuildResult) -> None:
    for operation in result.operations:
        print(f"{operation.stage}: {operation.action}")
        loudness = operation.details.get("loudness")
        if operation.stage == "composition" and isinstance(loudness, dict):
            _print_loudness_summary(public_api.LoudnessSummary.from_mapping(loudness))


def _cmd_compose(args: argparse.Namespace) -> int:
    app = _api_for(args)
    progress = _build_progress(args)
    with progress:
        result = app.projects.compose(
            args.project or Path.cwd(),
            public_api.CompositionOptions(
                mastering=args.mastering,
                target_lufs=args.target_lufs,
                true_peak_ceiling_dbtp=args.true_peak_ceiling_dbtp,
                peak_policy=args.peak_policy,
                clip_policy=args.clip_policy,
                sample_rate=args.sample_rate,
            ),
            on_event=_api_progress_handler(progress),
        )
    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "ok": True,
                    "composition_id": result.composition_id,
                    "master": str(result.master_path) if result.master_path is not None else None,
                    "frames": result.frames,
                    "items": result.items,
                    "mastering_profile": (
                        result.loudness.profile if result.loudness is not None else None
                    ),
                    "loudness": result.loudness.to_dict() if result.loudness is not None else None,
                },
                ensure_ascii=False,
            )
        )
    else:
        print(f"Composition: {result.composition_id}")
        if result.master_path is not None:
            print(f"Master: {result.master_path}")
        if result.loudness is not None:
            _print_loudness_summary(result.loudness)
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    result = _api_for(args).projects.export(
        args.project or Path.cwd(),
        public_api.ExportOptions(
            format=args.format,
            bitrate=args.bitrate,
            output=args.output,
            force=args.force,
        ),
    )
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, **result.to_dict()}, ensure_ascii=False))
    else:
        print(result.output_path)
    return 0


def _cmd_preview(args: argparse.Namespace) -> int:
    app = _api_for(args)
    progress = _build_progress(args)
    request = public_api.PreviewRequest(
        selection=args.select,
        voice_bindings=_parse_voice_bindings(getattr(args, "voice_bind", [])),
        synthesis=_project_synthesis_request(args),
        composition=public_api.CompositionOptions(
            mastering=getattr(args, "mastering", "spoken-word"),
            target_lufs=getattr(args, "target_lufs", None),
            true_peak_ceiling_dbtp=getattr(args, "true_peak_ceiling_dbtp", None),
            peak_policy=getattr(args, "peak_policy", "reduce_gain"),
            clip_policy=getattr(args, "clip_policy", "clamp"),
            sample_rate=getattr(args, "sample_rate", None),
        ),
        output=args.output,
        activate=args.activate,
    )
    with progress:
        result = app.projects.preview(
            args.project or Path.cwd(),
            request,
            on_event=_api_progress_handler(progress),
        )
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, **result.to_dict()}, ensure_ascii=False))
    else:
        print(
            f"Preview: {result.items} units, {result.rendered} synthesized, {result.reused} reused"
        )
        if result.output_path is not None:
            print(result.output_path)
        if result.loudness is not None:
            _print_loudness_summary(result.loudness)
    return 0


def _cmd_project_render(args: argparse.Namespace) -> int:
    app = _api_for(args)
    progress = _build_progress(args)
    request = _project_build_request(args)
    with progress:
        result = app.projects.build(
            getattr(args, "project", None) or Path.cwd(),
            request,
            on_event=_api_progress_handler(progress),
        )
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, **result.to_dict()}, ensure_ascii=False))
    else:
        _print_project_build_result(result)
    return 0


def _cmd_project(args: argparse.Namespace) -> int:
    if args.project_command == "migrate":
        from .migrations import migrate_project

        backup = migrate_project(args.project)
        result = {
            "ok": True,
            "project": str(args.project or Path.cwd()),
            "migrated": backup is not None,
        }
        if backup is not None:
            result["backup"] = str(backup)
        if getattr(args, "json", False):
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(
                f"migrated project; backup: {backup}" if backup else "project already uses schema 3"
            )
        return 0
    if args.project_command != "init":
        raise ValueError(f"unknown project command: {args.project_command}")
    project = _api_for(args).projects.create(args.source, output=args.output)
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, **project.to_dict()}, ensure_ascii=False))
    else:
        print(project.root)
    return 0


def _cmd_project_settings(args: argparse.Namespace) -> int:
    app = _api_for(args)
    project_path = (
        getattr(args, "project", None) or getattr(args, "settings_project", None) or Path.cwd()
    )
    project = app.projects.open(project_path)
    action = getattr(args, "settings_action", None) or "show"
    if action == "show":
        settings = app.projects.settings(project)
    elif action == "clear":
        settings = app.projects.update_settings(
            project,
            public_api.ProjectSettingsPatch(**{args.section: None}),
        )
    elif action == "set":
        current = app.projects.settings(project)
        groups = (
            (
                "synthesis",
                public_api.ProjectSynthesisSettings,
                (
                    "engine",
                    "model",
                    "language",
                    "voice",
                    "speed",
                    "voice_level",
                    "pause_mode",
                    "unit",
                    "voice_file",
                    "voice_prompt",
                    "lexicons",
                    "clear_lexicons",
                ),
            ),
            (
                "composition",
                public_api.CompositionOptions,
                (
                    "mastering",
                    "target_lufs",
                    "true_peak_ceiling_dbtp",
                    "peak_policy",
                    "clip_policy",
                    "sample_rate",
                ),
            ),
            (
                "export",
                public_api.ExportOptions,
                ("export_format", "export_output", "export_bitrate"),
            ),
            (
                "audiobook_export",
                public_api.AudiobookExportOptions,
                (
                    "audiobook_output",
                    "audiobook_title",
                    "audiobook_author",
                    "audiobook_cover",
                    "audiobook_bitrate",
                ),
            ),
        )
        updates = {}
        for section, settings_type, fields in groups:
            values = {}
            for name in fields:
                value = getattr(args, name)
                if value is not None:
                    values[name.removeprefix("export_").removeprefix("audiobook_")] = value
            if section == "synthesis" and any(
                name in values for name in ("voice", "voice_file", "voice_prompt")
            ):
                values.update(
                    {
                        name: None
                        for name in ("voice", "voice_file", "voice_prompt")
                        if name not in values
                    }
                )
            if values:
                existing = getattr(current, section) or settings_type()
                updates[section] = replace(existing, **values)
        if not updates:
            raise ValueError("provide at least one project settings option")
        settings = app.projects.update_settings(project, public_api.ProjectSettingsPatch(**updates))
    else:
        raise ValueError(f"unknown project settings action: {action}")

    settings_value = {
        section: _json_value(value)
        for section in ("synthesis", "composition", "export", "audiobook_export")
        if (value := getattr(settings, section)) is not None
    }
    payload = {
        "ok": True,
        "project": str(project.root),
        "settings": settings_value,
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"Project settings: {project.root}")
        for section in ("synthesis", "composition", "export", "audiobook_export"):
            value = settings_value.get(section)
            rendered = (
                json.dumps(value, ensure_ascii=False) if value is not None else "not configured"
            )
            print(f"{section}: {rendered}")
    return 0


def _audiobook_chapter_json(chapter: public_api.AudiobookChapter) -> dict[str, object]:
    return {
        "number": chapter.number,
        "source_id": chapter.source_id,
        "title": chapter.title,
        "href": chapter.href,
        "parent_id": chapter.parent_id,
        "source_parent_id": chapter.source_parent_id,
        "level": chapter.level,
        "char_count": chapter.char_count,
        "diagnostics": [item.to_dict() for item in chapter.diagnostics],
    }


def _cmd_audiobook_chapters(args: argparse.Namespace) -> int:
    inspection = _api_for(args).audiobooks.inspect(args.source)
    result = {
        "ok": True,
        "source": str(inspection.source),
        "metadata": dict(inspection.metadata),
        "chapters": [_audiobook_chapter_json(chapter) for chapter in inspection.chapters],
    }
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False))
    else:
        metadata = inspection.metadata
        print(f"Book: {metadata.get('title') or inspection.source.stem}")
        authors = metadata.get("authors", [])
        if authors:
            print(f"Author: {', '.join(authors)}")
        print(f"Chapters: {len(inspection.chapters)}")
        print()
        for chapter in inspection.chapters:
            indentation = "  " * max(0, chapter.level - 1)
            print(f"  {chapter.number:>2} {indentation}{chapter.title}")
    return 0


def _cmd_audiobook_init(args: argparse.Namespace) -> int:
    app = _api_for(args)
    result = app.audiobooks.create_project_result(
        args.source, chapters=args.chapters, language=args.language, output=args.output
    )
    project = result.project
    payload = {
        "ok": True,
        "project": str(project.root),
        "source": str(result.source),
        "selected_chapters": result.selected_chapters,
        "chapters": [
            {
                "number": chapter.number,
                "scope_id": chapter.scope_id,
                "title": chapter.title,
            }
            for chapter in result.chapters
        ],
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"Project: {project.root}")
        print(f"Source:  {result.source}")
        print(f"Selected chapters: {result.selected_chapters}")
        print()
        for chapter in result.chapters:
            indentation = "  " * max(0, chapter.level - 1)
            print(f"  {chapter.number:>2} {indentation}{chapter.title}")
    return 0


def _cmd_audiobook_export(args: argparse.Namespace) -> int:
    result = _api_for(args).audiobooks.export(
        args.project or Path.cwd(),
        public_api.AudiobookExportOptions(
            format=args.format,
            output=args.output,
            title=args.title,
            author=args.author,
            cover=args.cover,
            bitrate=args.bitrate,
            force=args.force,
        ),
    )
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, **result.to_dict()}, ensure_ascii=False))
    else:
        print(result.output_path)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    app = _api_for(args)
    project = app.projects.open(getattr(args, "project", None))
    result = app.projects.status(project)
    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), ensure_ascii=False))
    else:
        print(f"Readio project: {result.project.name}")
        print(f"Root: {result.project.root}")
        print(f"Source format: {result.project.source_format}\n")
        message_by_reason = {issue.code: issue.message for issue in result.issues}
        for row in result.stages:
            reusable = row.details.get("reusable")
            total = row.details.get("total")
            details = f" ({reusable}/{total} units reusable)" if reusable is not None else ""
            if row.blocked_by:
                details += f" blocked by {row.blocked_by}"
            human_reason = (
                "" if row.state == "current" else message_by_reason.get(row.reason, row.reason)
            )
            print(f"{row.stage.upper():<12} {row.state:<7} {human_reason}{details}".rstrip())
        print()
        if result.next_actions:
            action = result.next_actions[0]
            print("Next:")
            if action.command:
                print(f"  {action.command}")
            else:
                print(f"  {action.stage}: {action.reason}")
        else:
            print("Project is fully built.")
    return 0


def _plan_project_path(args: argparse.Namespace) -> Path:
    positional = getattr(args, "project_pos", None)
    option = getattr(args, "project_option", None)
    if positional is None and option is None:
        positional = getattr(args, "project", None)
    if positional is not None and option is not None:
        raise ValueError("specify the project once, either as a positional path or with --project")
    return positional or option or Path.cwd()


def _cmd_plan_build(args: argparse.Namespace) -> int:
    app = _api_for(args)
    project_path = _plan_project_path(args)
    progress = _build_progress(args)
    with progress:
        result = app.projects.plan(
            project_path,
            on_event=_api_progress_handler(progress),
        )
    scope_rows = [item.to_dict() for item in result.scopes]
    payload = {
        "ok": True,
        **result.to_dict(),
        "units": sum(item.units or 0 for item in result.scopes),
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False))
    elif len(scope_rows) == 1:
        item = result.scopes[0]
        print(f"Semantic plan: {item.plan_id}")
        print(f"Units: {item.units or 0}")
    else:
        print(f"Semantic plans: {len(scope_rows)} scopes")
        for item in result.scopes:
            print(f"{item.scope_id}: {item.plan_id} ({item.units or 0} units)")
    return 0


def _cmd_plan_roles(args: argparse.Namespace) -> int:
    app = _api_for(args)
    project = app.projects.open(_plan_project_path(args))
    inspection = app.roles.inspect_project(project, engine=getattr(args, "engine", None))
    result = {"ok": True, "project": str(project.root), **inspection.to_dict()}
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False))
        return 0
    print(f"Project:  {project.name}")
    print(f"Engine filter: {getattr(args, 'engine', None) or 'all'}")
    print(f"SSMD roles: {len(inspection.roles)}")
    print()
    print(f"{'ROLE':<12} {'USES':>4}  {'ENGINE':<12} {'VOICE':<24} SOURCE")
    print(f"{'-' * 12} {'-' * 4}  {'-' * 12} {'-' * 24} {'-' * 10}")
    for role in inspection.roles:
        target = role.effective_target
        engine = target.engine if target is not None else "-"
        voice = target.voice if target is not None else "-"
        source = _project_role_source(role)
        print(f"{role.role:<12} {role.uses:>4}  {engine:<12} {voice:<24} {source}")
    if inspection.unresolved:
        print()
        print(f"Unresolved roles: {', '.join(inspection.unresolved)}")
        print("Choose a voice:")
        print("  readio voices list --lang en-us")
        print("Bind them to this project:")
        for role in inspection.unresolved:
            print(f"  readio plan bind {role} <voice>")
    return 0


def _project_role_source(role: Any) -> str:
    if role.status == "mixed" and role.document_bindings:
        return "document"
    if role.origin == "config.voice_role":
        return "config"
    return role.origin


def _role_target_payload(target: Any) -> dict[str, Any] | None:
    if target is None:
        return None
    return target.to_dict()


def _cmd_plan_bind(args: argparse.Namespace) -> int:
    app = _api_for(args)
    project_path = _plan_project_path(args)
    result = app.roles.bind_project(
        project_path,
        args.role,
        args.voice,
        engine=getattr(args, "engine", None),
        discovery=public_api.DiscoveryOptions(
            offline=bool(args.offline), refresh=bool(args.refresh)
        ),
    )
    project_ref = app.projects.open(project_path)
    target = getattr(result, "project_target", None) or getattr(result, "effective_target", None)
    target_payload = _role_target_payload(target)
    result_data = {
        "ok": True,
        "project": str(project_ref.root),
        "role": result.role,
        "stored_voice": result.project_binding,
        "stored_target": target_payload,
        "engine": target.engine if target is not None else None,
    }
    if getattr(args, "json", False):
        print(json.dumps(result_data, ensure_ascii=False))
    else:
        print(f"Project: {project_ref.root.name}")
        if target is not None:
            print(f"{result.role} -> {target.engine}:{target.voice}")
        else:
            print(f"{result.role} -> {result.project_binding}")
        print("Source: project")
        print()
        print("Semantic plan unchanged.")
        print("Active synthesis must be refreshed if one exists.")
    return 0


def _cmd_plan_unbind(args: argparse.Namespace) -> int:
    app = _api_for(args)
    project_path = _plan_project_path(args)
    mutation = app.roles.unbind_project_result(
        project_path, args.role, engine=getattr(args, "engine", None)
    )
    previous_target = getattr(mutation, "previous_project_target", None)
    effective_target = getattr(mutation, "effective_target", None)
    result = {
        "ok": True,
        "project": str(mutation.project.root),
        "role": mutation.role,
        "removed_voice": mutation.previous_project_binding,
        "removed_target": _role_target_payload(previous_target),
        "effective_voice": mutation.effective_voice,
        "effective_target": _role_target_payload(effective_target),
        "origin": mutation.origin,
    }
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("Removed project binding:")
        if previous_target is not None:
            print(f"  {result['role']} -> {previous_target.engine}:{previous_target.voice}")
        else:
            print(f"  {result['role']} -> {result['removed_voice']}")
        print()
        if mutation.status == "unresolved":
            print(f"Role {mutation.role!r} is now unresolved.")
        elif mutation.status == "mixed":
            print(f"Role {mutation.role!r} has mixed effective targets across scopes.")
        elif effective_target is not None:
            print(f"Effective binding is now: {effective_target.engine}:{effective_target.voice}")
        else:
            print(f"Effective binding is now: {mutation.effective_voice} ({mutation.origin})")
    return 0


def _render_cli_live(args: argparse.Namespace, app: public_api.Readio) -> int:
    """Render live stdin to a Readio-owned file."""
    _validate_live(args)
    progress = _build_progress(args)
    with progress:
        progress.phase("Preparing", _progress_source_label(args))
        result = app.speech.render_live_to_file(
            sys.stdin,
            public_api.OutputRequest(
                requested_format=args.format,
                requested_path=args.output,
                force=args.force,
            ),
            synthesis=_synthesis_request_from_args(args, default_language=app.config.reader.lang),
            unit=args.unit,
            on_event=_api_progress_handler(progress),
        )
        progress.complete(result.summary)
    _emit_render_result(args, result)
    return 0


def _emit_render_result(args: argparse.Namespace, result: public_api.RenderResult) -> None:
    output = result.output_path
    audio_format = result.audio_format
    if output is None or audio_format is None:
        raise public_api.InvalidRequestError(
            "render result did not include an output path and audio format",
            code="render.result_incomplete",
        )
    if args.json:
        print(
            json.dumps(
                {
                    "ok": True,
                    "path": str(output),
                    "format": audio_format,
                    "sample_rate": result.summary.sample_rate,
                    "sample_count": result.summary.sample_count,
                    "channels": result.summary.channels,
                    "duration_ms": round(
                        result.summary.sample_count * 1000 / result.summary.sample_rate
                    ),
                    "markers": _json_value(result.summary.markers),
                    "manifest": (
                        {
                            "schema": result.manifest_schema,
                            "path": str(result.manifest_path),
                        }
                        if result.manifest_path is not None
                        else None
                    ),
                    "mastering_profile": (
                        result.loudness.profile if result.loudness is not None else None
                    ),
                    "loudness": result.loudness.to_dict() if result.loudness is not None else None,
                },
                ensure_ascii=False,
            )
        )
    else:
        print(output)
        if result.loudness is not None:
            _print_loudness_summary(result.loudness)


def _cmd_render(args: argparse.Namespace) -> int:
    """Render bounded input or orchestrate a persistent project."""
    positional = tuple(getattr(args, "text", ()) or ())
    if args.live and getattr(args, "manifest", False):
        raise ValueError(
            "--manifest is not available with --live because live rendering "
            "does not execute a bounded ReadioPlan"
        )
    app = _api_for(args)
    if not args.live and len(positional) == 1 and getattr(args, "file", None) is None:
        candidate = Path(positional[0]).expanduser()
        project = app.projects.find(candidate) if candidate.is_dir() else None
        if project is not None:
            progress = _build_progress(args)
            with progress:
                result = app.projects.build(
                    project,
                    _project_build_request(args),
                    on_event=_api_progress_handler(progress),
                )
            if getattr(args, "json", False):
                print(json.dumps({"ok": True, **result.to_dict()}, ensure_ascii=False))
            else:
                _print_project_build_result(result)
            return 0
    if args.live:
        return _render_cli_live(args, app)

    dry_run = bool(getattr(args, "dry_run", False))
    request = _build_plan_request(args, app, operation="render", allow_interactive=not dry_run)
    if dry_run:
        plan = app.speech.plan(request)
        if getattr(args, "json", False):
            print(json.dumps(plan.to_dict(), ensure_ascii=False, default=str))
        else:
            print(format_plan_human(plan))
        return 0 if plan.ok else 1

    progress = _build_progress(args)
    with progress:
        progress.phase("Planning", _progress_source_label(args))
        try:
            result = app.speech.render(
                request,
                on_event=_api_progress_handler(progress),
                write_manifest=bool(getattr(args, "manifest", False)),
            )
        except (public_api.PlanNotExecutableError, public_api.PlannedOutputError) as error:
            if getattr(args, "json", False):
                print(json.dumps(error.plan.to_dict(), ensure_ascii=False, default=str))
            else:
                print(format_plan_human(error.plan))
            return 1
        progress.complete(result.summary)

    _emit_render_result(args, result)
    return 0


def _cmd_ssmd(args: argparse.Namespace) -> int:
    app = _api_for(args)
    cfg = app.config
    if args.ssmd_command == "bind":
        result = app.ssmd.materialize_bindings(
            args.file,
            _parse_voice_bindings(args.voice_bind),
            provider=args.provider,
            output=args.output,
            in_place=args.in_place,
        )
        print(result.output_path)
        return 0

    bindings = _parse_voice_bindings(getattr(args, "voice_bind", []))
    synthesis = _synthesis_request_from_args(args, default_language=cfg.reader.lang)
    if args.resolve_voices:
        checked = app.ssmd.check(
            args.file,
            synthesis=synthesis,
            bindings=bindings,
        )
        if checked.analysis.unresolved_references:
            if args.json or not sys.stdin.isatty():
                raise ValueError(
                    "--resolve-voices requires an interactive terminal; "
                    "provide --voice-bind ROLE=VOICE_ID instead"
                )
            bindings.update(_prompt_for_missing_voices(app, checked.analysis, synthesis))
    result = app.ssmd.validate(
        args.file,
        synthesis=synthesis,
        bindings=bindings,
        roundtrip=bool(args.roundtrip),
    )
    analysis = result.analysis
    consumer = {
        "ok": analysis.ok,
        "unresolved": list(analysis.unresolved_references),
        "references": [
            {"name": item.reference, "count": item.count, "lines": list(item.lines)}
            for item in analysis.voice_references
        ],
        "diagnostics": [item.to_dict() for item in analysis.diagnostics],
    }
    payload = {
        "ok": result.ok,
        "source": str(result.source_path),
        "provider": analysis.provider,
        "consumer": consumer,
        "bindings": {
            "document": dict(analysis.document_bindings),
            "defaults": dict(analysis.default_bindings),
        },
        "roundtrip": result.roundtrip,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"Source: {result.source_path}")
        print(f"Provider: {analysis.provider}")
        print(f"Document bindings: {dict(analysis.document_bindings)}")
        print(f"Readio defaults: {dict(analysis.default_bindings)}")
        print("Consumer: OK" if consumer["ok"] else "Consumer: FAILED")
        if args.roundtrip:
            print("Roundtrip: OK" if (result.roundtrip or {}).get("ok") else "Roundtrip: FAILED")
    return 0


def _language_settings_payload(
    settings: public_api.LanguageSettings | None,
) -> dict[str, object] | None:
    if settings is None:
        return None
    return {
        "model": settings.model,
        "source": settings.source,
        "quality": settings.quality,
        "voice": settings.voice,
        "lexicons": list(settings.lexicons) if settings.lexicons is not None else None,
        "g2p_fallback": settings.g2p_fallback,
        "lexicon_data_policy": settings.lexicon_data_policy,
        "allow_experimental": settings.allow_experimental,
    }


def _print_language_settings(settings: public_api.LanguageSettings) -> None:
    print(f"Model:           {settings.model or '-'}")
    print(f"Source:          {settings.source or '-'}")
    print(f"Quality:         {settings.quality or '-'}")
    print(f"Voice:           {settings.voice or '-'}")
    print(f"Lexicons:        {_lexicons_label(settings.lexicons)}")
    print(f"Allow experimental: {'yes' if settings.allow_experimental else 'no'}")


def _cmd_defaults(args: argparse.Namespace) -> int:
    config = _api_for(args).configuration
    resolution = (
        config.resolve_language_profile(args.language) if getattr(args, "language", None) else None
    )
    language = resolution.normalized if resolution is not None else None
    if args.defaults_command == "list":
        profiles = config.language_profiles()
        items = [
            {"language": key, **(_language_settings_payload(value) or {})}
            for key, value in profiles.items()
        ]
        payload = {"ok": True, "defaults": items}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print("LANG  MODEL        VOICE     QUALITY  LEXICA  SOURCE")
            for item in items:
                print(
                    f"{item['language']:<5} {item['model'] or '-'!s: <12} "
                    f"{item['voice'] or '-'!s: <9} {item['quality'] or '-'!s: <8} "
                    f"{_lexicons_label(item['lexicons']):<7} {item['source'] or '-'}"
                )
        return 0

    assert resolution is not None
    assert language is not None
    if args.defaults_command == "show":
        settings = resolution.settings
        payload = {
            "ok": True,
            "language": language,
            "matched_key": resolution.matched_key,
            "match": resolution.match,
            "profile": _language_settings_payload(settings),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        print(f"Language:        {language}")
        print(f"Matched default: {resolution.matched_key or '-'}")
        if settings is None:
            print("No persisted language default.")
        else:
            _print_language_settings(settings)
        return 0

    if args.defaults_command == "reset":
        config.reset_language_profile(language)
        payload = {
            "ok": True,
            "language": language,
            "reset": True,
            "path": str(config.path()),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"Removed language default: {language}")
        return 0

    if args.lexicons is not None:
        lexicons = tuple(args.lexicons)
    elif args.no_lexicons:
        lexicons = ()
    elif args.auto_lexicons:
        lexicons = None
    else:
        lexicons = public_api.UNSET
    patch = public_api.LanguageProfilePatch(
        model=args.model if args.model is not None else public_api.UNSET,
        source=args.model_source if args.model_source is not None else public_api.UNSET,
        quality=args.quality if args.quality is not None else public_api.UNSET,
        voice=args.voice if args.voice is not None else public_api.UNSET,
        lexicons=lexicons,
        g2p_fallback=(args.g2p_fallback if args.g2p_fallback is not None else public_api.UNSET),
        lexicon_data_policy=(
            args.lexicon_data_policy if args.lexicon_data_policy is not None else public_api.UNSET
        ),
        allow_experimental=(
            args.allow_experimental if args.allow_experimental is not None else public_api.UNSET
        ),
    )
    settings = config.update_language_profile(
        language,
        patch,
        validate_runtime=True,
        discovery=public_api.DiscoveryOptions(
            offline=bool(args.offline),
            refresh=bool(args.refresh),
            preference=args.model_source or "auto",
        ),
    )
    payload = {
        "ok": True,
        "language": language,
        "profile": _language_settings_payload(settings),
        "path": str(config.path()),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"Saved language default: {language}")
        _print_language_settings(settings)
    return 0


def _lexicons_label(lexicons: tuple[str, ...] | None) -> str:
    if lexicons is None:
        return "unknown"
    return ", ".join(lexicons) or "-"


def _model_cli_dict(model: public_api.ModelInfo) -> dict[str, object]:
    payload = dict(model.to_dict())
    payload["lexicons_known"] = model.lexicons is not None
    return payload


def _voice_cli_dict(entry: public_api.VoiceInfo) -> dict[str, object]:
    return dict(entry.to_dict())


def _cmd_models(args: argparse.Namespace) -> int:
    app = _api_for(args)
    discovery = public_api.DiscoveryOptions(
        offline=bool(args.offline),
        refresh=bool(args.refresh),
        preference=args.preference,
    )
    if args.models_command == "list":
        listing = app.catalog.models_listing(
            public_api.ModelQuery(language=args.language, status=args.status),
            discovery=discovery,
        )
        models = listing.items
        payload = {
            "ok": True,
            "registry": listing.discovery.to_dict(),
            "models": [_model_cli_dict(model) for model in models],
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        if listing.discovery.cache_fallback:
            print(
                "Warning: remote registry unavailable; using cached registry.",
                file=sys.stderr,
            )
        if not models:
            print("No models matched.")
            return 0
        print(
            "MODEL          LANGUAGES  DEFAULT   VOICES  G2P        LEXICA                 QUALITIES  STATUS"
        )
        for model in models:
            languages = ", ".join(model.languages) or "-"
            voices = ", ".join(model.voices) if len(model.voices) <= 3 else str(len(model.voices))
            g2p = model.g2p_backend or "-"
            qualities = ", ".join(model.qualities) or "-"
            print(
                f"{model.id:<14} {languages:<9} {model.default_voice:<9} "
                f"{voices:<7} {g2p:<10} {_lexicons_label(model.lexicons):<22} "
                f"{qualities:<9} {model.status}"
            )
        return 0

    listing = app.catalog.model_listing(args.model_id, discovery=discovery)
    model = listing.items[0]
    payload = {
        "ok": True,
        "registry": listing.discovery.to_dict(),
        "model": _model_cli_dict(model),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if listing.discovery.cache_fallback:
        print(
            "Warning: remote registry unavailable; using cached registry.",
            file=sys.stderr,
        )
    print(f"Model:          {model.id}")
    print(f"ID:              {model.id}")
    print(f"Source:          {model.source}")
    print(f"Distribution:    {model.distribution_id or '-'}")
    print(f"Provider:        {model.provider or '-'}")
    print(f"Sample rate:     {f'{model.sample_rate} Hz' if model.sample_rate is not None else '-'}")
    print(f"Max tokens:      {model.max_tokens if model.max_tokens is not None else '-'}")
    print(f"Languages:       {', '.join(model.languages) or '-'}")
    print(f"Frontend:        {model.frontend or 'unknown'}")
    print(f"Status:          {model.status}")
    print(f"Experimental:    {'yes' if model.experimental else 'no'}")
    print(f"Runtime available: {'yes' if model.runtime_available else 'no'}")
    print(f"Redistribution allowed: {'yes' if model.redistribution_allowed else 'no'}")
    print(f"Default voice:  {model.default_voice}")
    print("Voices:")
    for voice in model.voices:
        print(f"  - {voice}")
    print("Qualities:")
    for quality in model.qualities:
        print(f"  - {quality}")
    print(f"G2P backend:     {model.g2p_backend or 'unknown'}")
    print(f"Lexicons:        {_lexicons_label(model.lexicons)}")
    if model.lexicons:
        for lexicon in model.lexicons:
            print(f"  - {lexicon}")
    return 0


def _voice_entry_human(entry: public_api.VoiceInfo) -> None:
    print(f"Voice ref:      {entry.ref}")
    print(f"Engine:         {entry.engine}")
    print(f"Target ID:      {entry.target_id}")
    print(f"Qualified ID:   {entry.qualified_id}")
    print(f"Voice:          {entry.id}")
    print(f"Gender:         {entry.gender}")
    print(f"Locale:         {entry.locale}")
    print(f"Language:       {entry.language_label}")
    print(f"Source:         {entry.source}")
    print(f"Default voice:  {'yes' if entry.default else 'no'}")
    print(f"Status:         {entry.status}")
    print(f"Experimental:   {'yes' if entry.experimental else 'no'}")
    print()
    print("Use with:")
    print(f"  readio speak --voice {entry.ref} <text>")


def _lexicon_entry_human(entry: public_api.LexiconInfo) -> None:
    print(f"Selector:       {entry.selector}")
    print(f"Engine:         {entry.engine}")
    print(f"Language:       {entry.language}")
    print(f"Locale:         {entry.locale}")
    print(f"Asset ID:       {entry.asset_id or '-'}")
    print(f"Data backend:   {entry.data_backend or '-'}")
    print(f"Display name:   {entry.display_name or '-'}")
    print(f"Phoneme format: {entry.phoneme_encoding or '-'}")
    print(f"Default:        {'yes' if entry.default else 'no'}")
    installed = "-" if entry.installed is None else "yes" if entry.installed else "no"
    print(f"Installed:      {installed}")
    print(f"Model support:  {entry.model_support}")
    print(f"Models:         {', '.join(entry.models) or '-'}")
    print()
    print(f"Use with:       --lexicon {entry.selector}")
    if entry.asset_id:
        print(f"Asset metadata: {entry.asset_id}")


def _cmd_lexicons(args: argparse.Namespace) -> int:
    language = getattr(args, "lang", None) or getattr(args, "language", None)
    app = _api_for(args)
    query = public_api.LexiconQuery(language=language, model=args.model, engine=args.engine)
    discovery = public_api.DiscoveryOptions(
        offline=bool(args.offline),
        refresh=bool(args.refresh),
        preference=args.preference,
    )
    filters = {"language": language, "model": args.model, "engine": args.engine}
    if args.lexicons_command == "list":
        listing = app.catalog.lexicons_listing(query, discovery=discovery)
        entries = listing.items
        payload = {
            "ok": True,
            "filters": filters,
            "registry": listing.discovery.to_dict(),
            "lexicons": [entry.to_dict() for entry in entries],
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        if listing.discovery.cache_fallback:
            print(
                "Warning: remote registry unavailable; using cached registry.",
                file=sys.stderr,
            )
        print(f"Lexicons: {len(entries)}")
        print()
        print("LEXICON  LOCALE  ASSET           ENGINE    MODELS  DEFAULT  DATA")
        print("-------  ------  --------------  --------  ------  -------  ---------")
        for entry in entries:
            models = ",".join(entry.models) or "-"
            installed = "available" if entry.installed is not False else "missing"
            print(
                f"{entry.selector:<8} {entry.locale:<7} {entry.asset_id or '-':<15} "
                f"{entry.engine:<9} {models:<7} {'yes' if entry.default else 'no':<8} {installed}"
            )
        return 0

    listing = app.catalog.lexicon_listing(args.selector, query=query, discovery=discovery)
    entry = listing.items[0]
    payload = {
        "ok": True,
        "filters": filters,
        "registry": listing.discovery.to_dict(),
        "lexicon": entry.to_dict(),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        _lexicon_entry_human(entry)
    return 0


def _normalize_voice_list_filters(
    *,
    engine: str | None,
    model: str | None,
    available_engines: set[str] | None,
    normalize_engine: Callable[[str], str],
) -> tuple[str | None, str | None]:
    available = available_engines or {"kokoro", "piper", "pocket", "kitten"}
    if engine is not None:
        canonical = normalize_engine(engine)
        if canonical == engine:
            folded = engine.casefold()
            folded_canonical = normalize_engine(folded)
            if folded_canonical != folded:
                canonical = folded_canonical
        return canonical, model
    if model is not None:
        canonical = normalize_engine(model.casefold())
        if canonical in available:
            return canonical, None
    return None, model


def _cmd_voices(args: argparse.Namespace) -> int:
    if hasattr(args, "roles_command"):
        return _cmd_roles(args)
    language = getattr(args, "lang", None) or getattr(args, "language", None)
    app = _api_for(args)
    discovery = public_api.DiscoveryOptions(
        offline=bool(args.offline),
        refresh=bool(args.refresh),
        preference=getattr(args, "preference", "auto"),
    )
    if args.voices_command == "prompts":
        engine = app.catalog.normalize_engine(args.engine)
        listing = app.catalog.voice_prompts_listing(
            public_api.VoicePromptQuery(
                engine=engine,
                dataset=args.dataset,
                variant=args.variant,
                license=args.license,
            ),
            discovery=discovery,
        )
        prompts = listing.items
        payload = {
            "ok": True,
            "registry": listing.discovery.to_dict(),
            "filters": {
                "engine": engine,
                "dataset": args.dataset,
                "variant": args.variant,
                "license": args.license,
            },
            "prompts": [item.to_dict() for item in prompts],
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        print(f"Voice prompts: {len(prompts)}")
        for prompt in prompts:
            print(
                f"{prompt.ref} [{prompt.dataset}/{prompt.variant}] "
                f"license={prompt.license} sha256={prompt.sha256} "
                f"revision={prompt.source_revision}"
            )
        return 0
    if args.voices_command == "list":
        available_engines = {item.id.casefold() for item in app.catalog.engines()}
        engine, model = _normalize_voice_list_filters(
            engine=args.engine,
            model=args.model,
            available_engines=available_engines,
            normalize_engine=app.catalog.normalize_engine,
        )
        listing = app.catalog.voices_listing(
            public_api.VoiceQuery(
                language=language, gender=args.gender, model=model, engine=engine
            ),
            discovery=discovery,
        )
        voices = listing.items
        payload = {
            "ok": True,
            "registry": listing.discovery.to_dict(),
            "filters": {
                "language": language,
                "gender": args.gender,
                "model": model,
                "engine": engine,
            },
            "voices": [_voice_cli_dict(entry) for entry in voices],
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        if listing.discovery.cache_fallback:
            print(
                "Warning: remote registry unavailable; using cached registry.",
                file=sys.stderr,
            )
        print(f"Voices: {len(voices)}")
        print()
        print(
            "VOICE REF                                 ENGINE   VOICE                 GENDER   "
            "LOCALE   LANGUAGE                 TARGET                 STATUS"
        )
        print(
            "----------------------------------------  -------  --------------------  -------  "
            "-------  -----------------------  ---------------------  ------------"
        )
        for entry in voices:
            print(
                f"{entry.ref:<40} {entry.engine:<8} {entry.id:<21} {entry.gender:<8} "
                f"{entry.locale:<8} {entry.language_label:<24} {entry.target_id:<22} {entry.status}"
            )
        return 0

    listing = app.catalog.voice_listing(
        args.reference,
        query=public_api.VoiceQuery(language=language, engine=args.engine),
        discovery=discovery,
    )
    entry = listing.items[0]
    payload = {
        "ok": True,
        "registry": listing.discovery.to_dict(),
        "voice": _voice_cli_dict(entry),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        _voice_entry_human(entry)
    return 0


def _cmd_roles(args: argparse.Namespace) -> int:
    app = _api_for(args)
    engine = getattr(args, "engine", None)
    if args.roles_command == "list":
        bindings = app.roles.list_global(engine=engine)
        roles = [binding.to_dict() for binding in bindings]
        result = {"ok": True, "engine": engine, "roles": roles}
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"Engine filter: {engine or 'all'}")
            print()
            print("ROLE        ENGINE       VOICE")
            print("----------  ------------ ------------------------")
            for binding in bindings:
                print(f"{binding.role:<11} {binding.engine:<12} {binding.voice}")
        return 0
    if args.roles_command == "bind":
        binding = app.roles.bind_global(
            args.role,
            args.voice_id,
            engine=engine,
        )
        result = {
            "ok": True,
            **binding.to_dict(),
            "path": app.configuration.path(),
        }
        if args.json:
            print(json.dumps(_json_value(result), ensure_ascii=False))
        else:
            print(f"{binding.role} -> {binding.engine}:{binding.voice}")
        return 0
    if args.roles_command == "unbind":
        binding = next(
            (item for item in app.roles.list_global(engine=engine) if item.role == args.role),
            None,
        )
        app.roles.unbind_global(args.role, engine=engine)
        result = {
            "ok": True,
            "role": args.role,
            "removed_target": binding.to_dict() if binding is not None else None,
            "engine": binding.engine if binding is not None else engine,
            "voice": binding.voice if binding is not None else None,
            "path": app.configuration.path(),
        }
        if args.json:
            print(json.dumps(_json_value(result), ensure_ascii=False))
        else:
            if binding is None:
                print(f"removed {args.role}")
            else:
                print(f"removed {args.role} ({binding.engine}:{binding.voice})")
        return 0
    raise AssertionError("unreachable")


def _cmd_config(args: argparse.Namespace) -> int:
    if args.config_command == "migrate":
        from .migrations import migrate_config_file

        backup = migrate_config_file(args.config_file)
        if backup is None:
            print("configuration already uses schema 3")
        else:
            print(f"migrated configuration; backup: {backup}")
        return 0
    app = _api_for(args)
    config = app.configuration
    path = config.path()
    if args.config_command == "path":
        print(path)
        return 0
    if args.config_command == "show":
        print(json.dumps(_json_value(config.load(path)), indent=2, ensure_ascii=False))
        return 0
    if args.config_command == "init":
        result = config.initialize(overwrite=args.force)
        print(result.path)
        return 0
    if args.config_command == "validate":
        cfg = config.validate(config.load(path))
        print(json.dumps({"ok": True, "paths": _json_value(cfg.paths)}, ensure_ascii=False))
        return 0
    if args.config_command == "set":
        config.set_value(args.key, args.value, path=path)
        print(path)
        return 0
    raise AssertionError("unreachable")


def _template_validation_json(
    result: public_api.TemplateValidationResult,
) -> dict[str, object]:
    consumer = result.consumer
    payload: dict[str, object] = {
        "name": result.name,
        "source": str(result.source_path),
        "ok": result.ok,
        "provider": consumer.provider if consumer is not None else None,
        "consumer": (
            {
                "ok": consumer.ok,
                "unresolved": list(consumer.unresolved_references),
                "diagnostics": [item.to_dict() for item in consumer.diagnostics],
            }
            if consumer is not None
            else None
        ),
        "bindings": (
            {
                "document": dict(consumer.document_bindings),
                "defaults": dict(consumer.default_bindings),
            }
            if consumer is not None
            else None
        ),
        "roundtrip": result.roundtrip,
    }
    if result.error is not None:
        payload["error"] = result.error.to_dict()
    return payload


def _cmd_template(args: argparse.Namespace) -> int:
    app = _api_for(args)
    templates = app.templates
    if args.template_command == "validate":
        if args.all:
            names = [item.name for item in templates.list()]
        elif args.name is not None:
            names = [Path(args.name).stem]
        else:
            raise ValueError("template validate requires NAME or --all")
        results = [templates.validate(name, roundtrip=args.roundtrip) for name in names]
        payload = {
            "ok": all(item.ok for item in results),
            "templates": [_template_validation_json(item) for item in results],
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            for item in results:
                status = "OK" if item.ok else "FAILED"
                print(f"{item.name}: {status}")
        return 0 if payload["ok"] else 2

    if args.template_command == "path":
        print(templates.path(args.name) if args.name else templates.directory())
    elif args.template_command == "list":
        for item in templates.list():
            print(item.name)
    elif args.template_command == "show":
        print(templates.show(args.name), end="")
    elif args.template_command == "add":
        source = Path(args.file) if args.file else None
        content = sys.stdin.read() if source is None and not sys.stdin.isatty() else None
        print(templates.add(args.name, source=source, content=content, force=args.force))
    elif args.template_command == "remove":
        templates.remove(args.name)
    elif args.template_command == "reset":
        if args.all:
            templates.reset(all=True)
        else:
            if args.name is None:
                raise ValueError("template reset requires NAME or --all")
            templates.reset(args.name)
    elif args.template_command == "use":
        target = app.ingest.create(name=args.name, template=args.name_template)
        print(target)
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    ingest = _api_for(args).ingest
    if args.ingest_command == "path":
        print(ingest.directory())
    elif args.ingest_command == "new":
        target = ingest.create(name=args.name, template=args.template)
        print(target)
    elif args.ingest_command == "list":
        for path in ingest.list():
            print(path.name)
    return 0


def _cmd_engines(args: argparse.Namespace) -> int:
    engines = _api_for(args).catalog.engines()
    if args.json:
        print(
            json.dumps(
                {"ok": True, "engines": [engine.to_dict() for engine in engines]},
                ensure_ascii=False,
            )
        )
        return 0

    print(f"{'ENGINE':<10} {'VERSION':<8} {'STATUS':<20} DEPENDENCY")
    for engine in engines:
        if engine.runnable:
            status = "ready"
        elif not engine.installed:
            status = "missing_dependency"
        else:
            status = "unavailable"
        print(
            f"{engine.id:<10} {engine.version or '-':<8} {status:<20} "
            f"{engine.missing_dependency or '-'}"
        )
    return 0


def _cmd_formats(args: argparse.Namespace) -> int:
    audio_formats = _api_for(args).catalog.audio_formats()
    audiobook_formats = [
        {"id": format_id, "suffix": f".{format_id}"}
        for format_id in public_api.SUPPORTED_AUDIOBOOK_FORMATS
    ]
    if args.json:
        print(
            json.dumps(
                {
                    "ok": True,
                    "audio": [item.to_dict() for item in audio_formats],
                    "audiobook": audiobook_formats,
                },
                ensure_ascii=False,
            )
        )
        return 0

    print("Generic audio formats")
    print(f"{'FORMAT':<8} {'SUFFIX':<8} STATUS")
    for item in audio_formats:
        status = "available" if item.available else "unavailable"
        print(f"{item.id:<8} {item.suffix:<8} {status}")
        if not item.available and item.reason:
            print(f"  {item.reason}")
    print()
    print("Audiobook formats")
    print(f"{'FORMAT':<8} SUFFIX")
    for item in audiobook_formats:
        print(f"{item['id']:<8} {item['suffix']}")
    return 0


def _cmd_doctor(args: argparse.Namespace | None) -> int:
    report = _api_for(args or argparse.Namespace()).diagnostics.run()
    if args is None or getattr(args, "json", False):
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return 0
    print(f"Readio {report.readio_version}")
    print(f"Config: {report.config_path} ({'exists' if report.config_exists else 'missing'})")
    for engine in report.engines:
        version = f" {engine.version}" if engine.version else ""
        print(f"Engine {engine.id}: {engine.status}{version}")
    available = [item.id for item in report.audio_formats if item.available]
    print(f"Audio formats: {', '.join(available)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = ReadioArgumentParser(
        prog="readio",
        usage="%(prog)s [OPTIONS] COMMAND [ARGS]...",
        description=(
            "Plan, synthesize, compose, and export speech and audiobooks with multiple TTS engines."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="show internal diagnostics; repeat (-vv) for debug-level detail",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="request machine-readable output when supported by the selected command",
    )
    sub = parser.add_subparsers(dest="command", required=True, title="Commands", metavar="COMMAND")

    speak = sub.add_parser("speak", help="Play text or a document as speech.")
    _add_input_options(speak)
    _add_runtime_options(speak)
    speak.set_defaults(func=_cmd_speak)

    render = sub.add_parser("render", help="Render text or a document to an audio file.")
    _add_input_options(render)
    _add_audio_output_options(render)
    render.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output audio path (.wav, .mp3, .m4a, or .ogg)",
    )
    _add_mastering_options(render)
    render.add_argument("--force", action="store_true", help="replace an existing output")
    _add_runtime_options(render, playback=False)
    _add_progress_option(render)
    render.add_argument("--json", action="store_true", help="emit one JSON result object")
    render.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve and display the plan without loading TTS or creating output",
    )
    render.add_argument(
        "--manifest",
        action="store_true",
        help="write <audio>.readio.json with the executed plan and actual render metadata",
    )
    render.set_defaults(func=_cmd_render)

    plan_cmd = sub.add_parser(
        "plan",
        help="Build project speech plans and manage project role voices.",
    )
    plan_cmd.add_argument("--json", action="store_true", help="emit JSON output")
    _add_progress_option(plan_cmd)
    plan_cmd.set_defaults(func=_cmd_plan_build, project=None)
    plan_sub = plan_cmd.add_subparsers(dest="plan_action")

    plan_build = plan_sub.add_parser("build", help="build semantic plans for a project")
    plan_build.add_argument("project_pos", nargs="?", type=Path)
    plan_build.add_argument("--project", dest="project_option", type=Path)
    _add_progress_option(plan_build, default=argparse.SUPPRESS)
    plan_build.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    plan_build.set_defaults(func=_cmd_plan_build)

    plan_roles = plan_sub.add_parser("roles", help="inspect SSMD roles and effective voices")
    plan_roles.add_argument("project_pos", nargs="?", type=Path)
    plan_roles.add_argument("--project", dest="project_option", type=Path)
    plan_roles.add_argument("--engine", help="filter by canonical engine ID")
    plan_roles.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    plan_roles.set_defaults(func=_cmd_plan_roles)

    plan_bind = plan_sub.add_parser(
        "bind", help="bind a project role to an engine-qualified voice target"
    )
    plan_bind.add_argument("role")
    plan_bind.add_argument("voice")
    plan_bind.add_argument("project_pos", nargs="?", type=Path)
    plan_bind.add_argument("--engine", help="canonical engine ID for raw voice IDs")
    plan_bind.add_argument("--project", dest="project_option", type=Path)
    plan_bind.add_argument(
        "--offline", action="store_true", help="use cached discovery metadata only"
    )
    plan_bind.add_argument(
        "--refresh", action="store_true", help="refresh voice discovery metadata"
    )
    plan_bind.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    plan_bind.set_defaults(func=_cmd_plan_bind)

    plan_unbind = plan_sub.add_parser("unbind", help="remove a project role voice binding")
    plan_unbind.add_argument("role")
    plan_unbind.add_argument("project_pos", nargs="?", type=Path)
    plan_unbind.add_argument("--engine", help="filter by canonical engine ID")
    plan_unbind.add_argument("--project", dest="project_option", type=Path)
    plan_unbind.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    plan_unbind.set_defaults(func=_cmd_plan_unbind)
    project_cmd = sub.add_parser("project", help="Create and manage persistent Readio projects.")
    project_cmd.set_defaults(func=show_help, _help_parser=project_cmd)
    project_sub = project_cmd.add_subparsers(
        dest="project_command", required=False, title="Commands", metavar="COMMAND"
    )
    project_init = project_sub.add_parser(
        "init", help="initialize a project from a document supported by ssmdconvert"
    )
    project_init.add_argument("source", type=Path)
    project_init.add_argument("-o", "--output", type=Path)
    project_init.add_argument("--json", action="store_true")
    project_init.set_defaults(func=_cmd_project)
    project_migrate = project_sub.add_parser(
        "migrate", help="migrate a v0.3 project to schema 3 with a backup"
    )
    project_migrate.add_argument("project", nargs="?", type=Path)
    project_migrate.add_argument("--json", action="store_true")
    project_migrate.set_defaults(func=_cmd_project)
    project_settings = project_sub.add_parser(
        "settings", help="inspect or configure saved project pipeline settings"
    )
    project_settings.add_argument("--project", type=Path, dest="settings_project")
    project_settings.add_argument("--json", action="store_true")
    project_settings.set_defaults(func=_cmd_project_settings, settings_action="show")
    settings_sub = project_settings.add_subparsers(
        dest="settings_action", title="Settings actions", metavar="ACTION"
    )
    settings_show = settings_sub.add_parser("show", help="show saved project pipeline settings")
    settings_show.add_argument("project", nargs="?", type=Path)
    settings_show.add_argument("--json", action="store_true")
    settings_show.set_defaults(func=_cmd_project_settings, settings_action="show")
    settings_set = settings_sub.add_parser("set", help="set supported pipeline settings")
    settings_set.add_argument("project", nargs="?", type=Path)
    settings_set.add_argument("--json", action="store_true")
    settings_set.add_argument("--engine")
    settings_set.add_argument("--model")
    settings_set.add_argument("--language")
    settings_voice_group = settings_set.add_mutually_exclusive_group()
    settings_voice_group.add_argument("--voice")
    settings_set.add_argument("--speed", type=float)
    settings_set.add_argument("--voice-level", choices=("off", "calibrated"))
    settings_set.add_argument("--pause-mode", choices=("tts", "manual", "auto"))
    settings_set.add_argument("--unit", choices=("sentence", "paragraph"))
    settings_voice_group.add_argument("--voice-file", type=Path)
    settings_voice_group.add_argument("--voice-prompt")
    settings_set.add_argument("--lexicon", action="append", dest="lexicons")
    settings_set.add_argument("--clear-lexicons", action="store_true", default=None)
    settings_set.add_argument("--mastering", choices=MASTERING_PROFILE_CHOICES)
    settings_set.add_argument("--target-lufs", type=float)
    settings_set.add_argument("--true-peak-ceiling-dbtp", type=float)
    settings_set.add_argument("--peak-policy", choices=("reduce_gain", "error"))
    settings_set.add_argument("--clip-policy", choices=("clamp", "error"))
    settings_set.add_argument("--sample-rate", type=int)
    settings_set.add_argument("--export-format", choices=public_api.SUPPORTED_AUDIO_FORMATS)
    settings_set.add_argument("--export-output", type=Path)
    settings_set.add_argument("--export-bitrate")
    settings_set.add_argument("--audiobook-output", type=Path)
    settings_set.add_argument("--audiobook-title")
    settings_set.add_argument("--audiobook-author")
    settings_set.add_argument("--audiobook-cover", type=Path)
    settings_set.add_argument("--audiobook-bitrate")
    settings_set.set_defaults(func=_cmd_project_settings, settings_action="set")
    settings_clear = settings_sub.add_parser("clear", help="clear one saved settings section")
    settings_clear.add_argument("project", nargs="?", type=Path)
    settings_clear.add_argument(
        "--section",
        required=True,
        choices=("synthesis", "composition", "export", "audiobook_export"),
    )
    settings_clear.add_argument("--json", action="store_true")
    settings_clear.set_defaults(func=_cmd_project_settings, settings_action="clear")
    audiobook_cmd = sub.add_parser(
        "audiobook", help="Inspect book sources and create/export audiobook projects."
    )
    audiobook_cmd.set_defaults(func=show_help, _help_parser=audiobook_cmd)
    audiobook_sub = audiobook_cmd.add_subparsers(
        dest="audiobook_command", required=False, title="Commands", metavar="COMMAND"
    )
    audiobook_chapters = audiobook_sub.add_parser("chapters", help="list selectable book chapters")
    audiobook_chapters.add_argument("source", type=Path)
    audiobook_chapters.add_argument("--json", action="store_true")
    audiobook_chapters.set_defaults(func=_cmd_audiobook_chapters)
    audiobook_init = audiobook_sub.add_parser(
        "init", help="attach a book workspace or create an editable .ssmdbook workspace"
    )
    audiobook_init.add_argument("source", type=Path)
    audiobook_init.add_argument("--chapters", default="all")
    audiobook_init.add_argument("--language", help="semantic language override for EPUB conversion")
    audiobook_init.add_argument("-o", "--output", type=Path)
    audiobook_init.add_argument("--json", action="store_true")
    audiobook_init.set_defaults(func=_cmd_audiobook_init)

    audiobook_export = audiobook_sub.add_parser(
        "export", help="export an audiobook project master as M4B"
    )
    audiobook_export.add_argument("project", nargs="?", type=Path)
    audiobook_export.add_argument(
        "--format",
        choices=public_api.SUPPORTED_AUDIOBOOK_FORMATS,
        default=public_api.AUDIOBOOK_EXPORT_FORMAT,
    )
    audiobook_export.add_argument("--title")
    audiobook_export.add_argument("--author")
    audiobook_export.add_argument("--cover", type=Path, help="cover image (.jpg or .png)")
    audiobook_export.add_argument("--bitrate", help="AAC target bitrate, default: 192k")
    audiobook_export.add_argument("-o", "--output", type=Path, help="M4B output path")
    audiobook_export.add_argument("--force", action="store_true", help="replace an existing output")
    audiobook_export.add_argument("--json", action="store_true")
    audiobook_export.set_defaults(func=_cmd_audiobook_export)

    status_cmd = sub.add_parser(
        "status", help="Show project pipeline state and recommended next actions."
    )
    status_cmd.add_argument("project", nargs="?", type=Path)
    status_cmd.add_argument("--json", action="store_true")
    status_cmd.set_defaults(func=_cmd_status)

    synth_cmd = sub.add_parser("synth", help="Synthesize missing or stale project speech.")
    synth_cmd.add_argument("project", nargs="?", type=Path)
    synth_cmd.add_argument("--select", default="all")
    _add_synthesis_options(synth_cmd)
    _add_voice_resolution_options(synth_cmd)
    _add_progress_option(synth_cmd)
    synth_cmd.add_argument("--json", action="store_true")
    synth_cmd.set_defaults(func=_cmd_synth)

    compose_cmd = sub.add_parser(
        "compose", help="Assemble synthesized project audio into a master."
    )
    compose_cmd.add_argument("project", nargs="?", type=Path)
    _add_mastering_options(compose_cmd)
    _add_progress_option(compose_cmd)
    compose_cmd.add_argument("--json", action="store_true")
    compose_cmd.set_defaults(func=_cmd_compose)

    export_cmd = sub.add_parser("export", help="Encode a composed project master.")
    export_cmd.add_argument("project", nargs="?", type=Path)
    export_cmd.add_argument("--format", choices=public_api.SUPPORTED_AUDIO_FORMATS, default="wav")
    export_cmd.add_argument("--bitrate")
    export_cmd.add_argument("-o", "--output", type=Path)
    export_cmd.add_argument("--force", action="store_true", help="replace an existing output")
    export_cmd.add_argument("--json", action="store_true")
    export_cmd.set_defaults(func=_cmd_export)

    preview_cmd = sub.add_parser("preview", help="Render a selected project range for review.")
    preview_cmd.add_argument("project", nargs="?", type=Path)
    preview_cmd.add_argument("--select", default="first:3")
    _add_mastering_options(preview_cmd)
    preview_cmd.add_argument("-o", "--output", type=Path)
    preview_cmd.add_argument("--activate", action="store_true")
    _add_synthesis_options(preview_cmd)
    _add_voice_resolution_options(preview_cmd)
    _add_progress_option(preview_cmd)
    preview_cmd.add_argument("--json", action="store_true")
    preview_cmd.set_defaults(func=_cmd_preview)
    from .spotify_cli import add_spotify_parser

    add_spotify_parser(sub)

    models = sub.add_parser("models", help="List synthesis models and targets.")
    models.set_defaults(func=show_help, _help_parser=models)
    models_sub = models.add_subparsers(
        dest="models_command", required=False, title="Commands", metavar="COMMAND"
    )
    models_list = models_sub.add_parser("list", help="list registry models and capabilities")
    models_list.add_argument("--language")
    models_list.add_argument("--status")
    models_list.add_argument("--offline", action="store_true")
    models_list.add_argument(
        "--preference", choices=("auto", "github", "huggingface", "upstream"), default="auto"
    )
    models_list.add_argument("--refresh", action="store_true")
    models_list.add_argument("--json", action="store_true")
    models_list.set_defaults(func=_cmd_models)
    models_show = models_sub.add_parser("show", help="show one model's capabilities")
    models_show.add_argument("model_id")
    models_show.add_argument("--offline", action="store_true")
    models_show.add_argument("--json", action="store_true")
    models_show.add_argument(
        "--preference", choices=("auto", "github", "huggingface", "upstream"), default="auto"
    )
    models_show.add_argument("--refresh", action="store_true")
    models_show.set_defaults(func=_cmd_models)

    engines = sub.add_parser("engines", help="Show synthesis engines known to Readio.")
    engines.add_argument("--json", action="store_true", help="emit one JSON result object")
    engines.set_defaults(func=_cmd_engines)

    formats = sub.add_parser("formats", help="List generic and audiobook output formats.")
    formats.add_argument("--json", action="store_true", help="emit one JSON result object")
    formats.set_defaults(func=_cmd_formats)

    lexicons = sub.add_parser("lexicons", help="List named pronunciation lexicons.")
    lexicons.set_defaults(func=show_help, _help_parser=lexicons)
    lexicons_sub = lexicons.add_subparsers(
        dest="lexicons_command", required=False, title="Commands", metavar="COMMAND"
    )
    lexicons_list = lexicons_sub.add_parser("list", help="list named lexicon selectors")
    lexicons_list.add_argument("--lang", "--language", dest="language")
    lexicons_list.add_argument("--model")
    lexicons_list.add_argument("--engine")
    lexicons_list.add_argument("--offline", action="store_true")
    lexicons_list.add_argument("--refresh", action="store_true")
    lexicons_list.add_argument(
        "--preference", choices=("auto", "github", "huggingface", "upstream"), default="auto"
    )
    lexicons_list.add_argument("--json", action="store_true")
    lexicons_list.set_defaults(func=_cmd_lexicons)
    lexicons_show = lexicons_sub.add_parser("show", help="show one named lexicon selector")
    lexicons_show.add_argument("selector")
    lexicons_show.add_argument("--lang", "--language", dest="language")
    lexicons_show.add_argument("--model")
    lexicons_show.add_argument("--engine")
    lexicons_show.add_argument("--offline", action="store_true")
    lexicons_show.add_argument("--refresh", action="store_true")
    lexicons_show.add_argument(
        "--preference", choices=("auto", "github", "huggingface", "upstream"), default="auto"
    )
    lexicons_show.add_argument("--json", action="store_true")
    lexicons_show.set_defaults(func=_cmd_lexicons)

    defaults = sub.add_parser("defaults", help="Manage per-language synthesis defaults.")
    defaults.set_defaults(func=show_help, _help_parser=defaults)
    defaults_sub = defaults.add_subparsers(
        dest="defaults_command", required=False, title="Commands", metavar="COMMAND"
    )
    defaults_list = defaults_sub.add_parser("list", help="list persisted language defaults")
    defaults_list.add_argument("--json", action="store_true")
    defaults_list.set_defaults(func=_cmd_defaults)
    defaults_show = defaults_sub.add_parser("show", help="show a language default")
    defaults_show.add_argument("language")
    defaults_show.add_argument("--json", action="store_true")
    defaults_show.set_defaults(func=_cmd_defaults)
    defaults_set = defaults_sub.add_parser("set", help="validate and save a language default")
    defaults_set.add_argument("language")
    defaults_set.add_argument("--model")
    defaults_set.add_argument("--model-source", choices=("github", "huggingface"))
    defaults_set.add_argument("--quality")
    defaults_set.add_argument("--voice")
    lexicon_group = defaults_set.add_mutually_exclusive_group()
    lexicon_group.add_argument(
        "--lexicon",
        dest="lexicons",
        action="append",
        metavar="NAME",
        help="named PyKokoro/KokoroG2P lexicon, e.g. crane; repeat for layered lookup",
    )
    lexicon_group.add_argument(
        "--no-lexicons",
        action="store_true",
        help="explicitly disable static lexicon layers (provider-only)",
    )
    lexicon_group.add_argument(
        "--auto-lexicons",
        action="store_true",
        help="remove the persisted lexicon override and use language defaults",
    )
    defaults_set.add_argument("--g2p-fallback", choices=public_api.G2P_FALLBACKS)
    defaults_set.add_argument("--lexicon-data-policy", choices=public_api.LEXICON_DATA_POLICIES)
    defaults_set.add_argument(
        "--allow-experimental",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    defaults_set.add_argument("--offline", action="store_true")
    defaults_set.add_argument("--refresh", action="store_true")
    defaults_set.add_argument("--json", action="store_true")
    defaults_set.set_defaults(func=_cmd_defaults)
    defaults_reset = defaults_sub.add_parser("reset", help="remove a language default")
    defaults_reset.add_argument("language")
    defaults_reset.add_argument("--json", action="store_true")
    defaults_reset.set_defaults(func=_cmd_defaults)

    voices = sub.add_parser("voices", help="List and inspect runnable voices.")
    voices.set_defaults(func=show_help, _help_parser=voices)
    voices_sub = voices.add_subparsers(
        dest="voices_command", required=False, title="Commands", metavar="COMMAND"
    )
    voices_list = voices_sub.add_parser("list", help="list runnable registry voices")
    voices_list.add_argument(
        "--lang",
        "--language",
        dest="lang",
        help="filter by language/locale; base codes include regional voices",
    )
    voices_list.add_argument(
        "--gender",
        choices=("female", "male", "neutral", "unknown"),
        help="filter by registry gender metadata",
    )
    voices_list.add_argument(
        "--model",
        help="filter by concrete model/target; registered engine names are shortcuts when --engine is omitted",
    )
    voices_list.add_argument(
        "--engine",
        help="filter by synthesis engine or system; takes precedence over engine-name model shortcuts",
    )
    voices_list.add_argument("--offline", action="store_true")
    voices_list.add_argument("--refresh", action="store_true")
    voices_list.add_argument(
        "--preference", choices=("auto", "github", "huggingface", "upstream"), default="auto"
    )
    voices_list.add_argument("--json", action="store_true")
    voices_list.set_defaults(func=_cmd_voices)
    voices_prompts = voices_sub.add_parser(
        "prompts", help="list metadata-only PocketSynth managed voice prompts"
    )
    voices_prompts.add_argument("--engine", choices=("pocket",), default="pocket")
    voices_prompts.add_argument("--dataset", help="filter by prompt dataset")
    voices_prompts.add_argument("--variant", help="filter by prompt variant")
    voices_prompts.add_argument("--license", help="filter by prompt license")
    voices_prompts.add_argument("--offline", action="store_true")
    voices_prompts.add_argument("--refresh", action="store_true")
    voices_prompts.add_argument("--json", action="store_true")
    voices_prompts.set_defaults(func=_cmd_voices)
    voices_show = voices_sub.add_parser(
        "show", help="show one semantic voice reference or native ID"
    )
    voices_show.add_argument("reference")
    voices_show.add_argument("--engine", help="filter by synthesis engine")
    voices_show.add_argument("--offline", action="store_true")
    voices_show.add_argument("--refresh", action="store_true")
    voices_show.add_argument(
        "--preference", choices=("auto", "github", "huggingface", "upstream"), default="auto"
    )
    voices_show.add_argument("--json", action="store_true")
    voices_show.set_defaults(func=_cmd_voices)

    roles = sub.add_parser("roles", help="Manage persistent SSMD role bindings.")
    roles.set_defaults(func=show_help, _help_parser=roles)
    roles_sub = roles.add_subparsers(
        dest="roles_command", required=False, title="Commands", metavar="COMMAND"
    )
    roles_list = roles_sub.add_parser("list", help="list configured logical roles")
    roles_list.add_argument("--engine", help="filter by canonical engine ID")
    roles_list.add_argument("--json", action="store_true")
    roles_list.set_defaults(func=_cmd_roles)
    roles_bind = roles_sub.add_parser(
        "bind", help="persist an engine-qualified logical role target"
    )
    roles_bind.add_argument("role")
    roles_bind.add_argument("voice_id")
    roles_bind.add_argument("--engine", help="canonical engine ID for raw voice IDs")
    roles_bind.add_argument("--json", action="store_true")
    roles_bind.set_defaults(func=_cmd_roles)
    roles_unbind = roles_sub.add_parser("unbind", help="remove a logical role binding")
    roles_unbind.add_argument("role")
    roles_unbind.add_argument("--engine", help="filter by canonical engine ID")
    roles_unbind.add_argument("--json", action="store_true")
    roles_unbind.set_defaults(func=_cmd_roles)

    ssmd = sub.add_parser("ssmd", help="Validate and author SSMD documents.")
    ssmd.set_defaults(func=show_help, _help_parser=ssmd)
    ssmd_sub = ssmd.add_subparsers(
        dest="ssmd_command", required=False, title="Commands", metavar="COMMAND"
    )
    bind = ssmd_sub.add_parser("bind", help="materialize explicit voice bindings")
    bind.add_argument("file", type=Path)
    bind.add_argument("--voice-bind", action="append", default=[], metavar="ROLE=VOICE_ID")
    bind.add_argument("--provider")
    bind.add_argument("-o", "--output", type=Path)
    bind.add_argument("--in-place", action="store_true")
    bind.set_defaults(func=_cmd_ssmd)
    check = ssmd_sub.add_parser("check", help="check an SSMD document for Readio consumption")
    check.add_argument("file", type=Path)
    check.add_argument("--roundtrip", action="store_true")
    check.add_argument("--json", action="store_true")
    _add_voice_resolution_options(check)
    check.set_defaults(func=_cmd_ssmd)

    cfg = sub.add_parser("config", help="Inspect and update Readio configuration.")
    cfg.set_defaults(func=show_help, _help_parser=cfg)
    cfg_sub = cfg.add_subparsers(
        dest="config_command", required=False, title="Commands", metavar="COMMAND"
    )
    cfg_path = cfg_sub.add_parser("path", help="Print the configuration file path.")
    cfg_path.set_defaults(func=_cmd_config)
    cfg_show = cfg_sub.add_parser("show", help="Show effective configuration as JSON.")
    cfg_show.set_defaults(func=_cmd_config)
    init = cfg_sub.add_parser("init", help="Write the default configuration.")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=_cmd_config)
    cfg_validate = cfg_sub.add_parser("validate", help="Validate the effective configuration.")
    cfg_validate.set_defaults(func=_cmd_config)
    cfg_migrate = cfg_sub.add_parser(
        "migrate", help="migrate a v0.3 config to schema 3 with a backup"
    )
    cfg_migrate.add_argument("config_file", nargs="?", type=Path)
    cfg_migrate.set_defaults(func=_cmd_config)
    set_cmd = cfg_sub.add_parser("set", help="Set one dotted configuration key.")
    set_cmd.add_argument("key")
    set_cmd.add_argument("value")
    set_cmd.set_defaults(func=_cmd_config)

    template = sub.add_parser("template", help="Manage user SSMD templates.")
    template.set_defaults(func=show_help, _help_parser=template)
    template_sub = template.add_subparsers(
        dest="template_command", required=False, title="Commands", metavar="COMMAND"
    )
    path_cmd = template_sub.add_parser(
        "path", help="Show the template directory or one template path."
    )
    path_cmd.add_argument("name", nargs="?")
    path_cmd.set_defaults(func=_cmd_template)
    template_list = template_sub.add_parser("list", help="List installed templates.")
    template_list.set_defaults(func=_cmd_template)
    validate_template = template_sub.add_parser(
        "validate", help="Validate one template or all installed templates."
    )
    validate_template.add_argument("name", nargs="?")
    validate_template.add_argument("--all", action="store_true")
    validate_template.add_argument("--roundtrip", action="store_true")
    validate_template.add_argument("--json", action="store_true")
    validate_template.set_defaults(func=_cmd_template)
    show_cmd = template_sub.add_parser("show", help="Show one template.")
    show_cmd.add_argument("name")
    show_cmd.set_defaults(func=_cmd_template)
    add_cmd = template_sub.add_parser("add", help="Add a user template.")
    add_cmd.add_argument("name")
    add_cmd.add_argument("--file", type=Path)
    add_cmd.add_argument("--force", action="store_true")
    add_cmd.set_defaults(func=_cmd_template)
    remove_cmd = template_sub.add_parser("remove", help="Remove a user template.")
    remove_cmd.add_argument("name")
    remove_cmd.set_defaults(func=_cmd_template)
    reset_cmd = template_sub.add_parser("reset", help="Reset one or all user templates.")
    reset_cmd.add_argument("name", nargs="?")
    reset_cmd.add_argument("--all", action="store_true")
    reset_cmd.set_defaults(func=_cmd_template)
    use_cmd = template_sub.add_parser("use", help="Create an ingest file from a template.")
    use_cmd.add_argument("name_template")
    use_cmd.add_argument("--name")
    use_cmd.set_defaults(func=_cmd_template)

    ingest = sub.add_parser("ingest", help="Manage Readio ingest files.")
    ingest.set_defaults(func=show_help, _help_parser=ingest)
    ingest_sub = ingest.add_subparsers(
        dest="ingest_command", required=False, title="Commands", metavar="COMMAND"
    )
    ingest_path = ingest_sub.add_parser("path", help="Show the ingest directory.")
    ingest_path.set_defaults(func=_cmd_ingest)
    new_cmd = ingest_sub.add_parser("new", help="Create a new ingest file.")
    new_cmd.add_argument("--name")
    new_cmd.add_argument("--template")
    new_cmd.set_defaults(func=_cmd_ingest)
    ingest_list = ingest_sub.add_parser("list", help="List ingest files.")
    ingest_list.set_defaults(func=_cmd_ingest)

    doctor = sub.add_parser("doctor", help="Check dependencies, engines, paths, and formats.")
    doctor.set_defaults(func=_cmd_doctor)
    doctor.add_argument("--json", action="store_true", help="emit one JSON result object")
    return parser


def _error_code(exc: Exception) -> str:
    if isinstance(exc, public_api.ReadioError):
        return exc.code
    if isinstance(exc, (ValueError, KeyError)):
        return "invalid_argument"
    if isinstance(exc, OSError):
        return "io_error"
    return "readio_error"


def _error_payload(exc: Exception) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": False,
        "code": _error_code(exc),
        "error": str(exc),
    }
    if isinstance(exc, public_api.ReadioError):
        payload.update(exc.details)
        if exc.source_path is not None:
            payload["source"] = str(exc.source_path)
    return payload


def _log_command_error(args: argparse.Namespace, exc: Exception) -> None:
    if getattr(args, "verbose", 0) >= MAX_VERBOSITY:
        logger.debug(
            "command.error command=%s error=%s",
            getattr(args, "command", "unknown"),
            exc,
            exc_info=(type(exc), exc, exc.__traceback__),
        )


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if not raw_argv:
        parser.print_help()
        raise SystemExit(0)
    normalized_argv, global_options = _extract_global_options(raw_argv)
    args = parser.parse_args(normalized_argv)
    if global_options.json:
        args.json = True
    args.verbose = min(max(getattr(args, "verbose", 0), global_options.verbosity), MAX_VERBOSITY)
    configure_logging(args.verbose)
    started = time.perf_counter()
    logger.info("command.start command=%s", args.command)
    try:
        code = args.func(args)
    except public_api.ReadioError as exc:
        _log_command_error(args, exc)
        if getattr(args, "json", False):
            print(json.dumps(_error_payload(exc), ensure_ascii=False))
            raise SystemExit(2)
        source = f" (source: {exc.source_path})" if exc.source_path else ""
        parser.exit(2, f"readio: {exc}{source}\n")
    except (ValueError, KeyError, OSError) as exc:
        _log_command_error(args, exc)
        if getattr(args, "json", False):
            print(json.dumps(_error_payload(exc), ensure_ascii=False))
            raise SystemExit(2)
        parser.exit(2, f"readio: {exc}\n")
    else:
        logger.info(
            "command.finish command=%s elapsed_ms=%.3f code=%s",
            args.command,
            (time.perf_counter() - started) * 1000.0,
            code,
        )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
