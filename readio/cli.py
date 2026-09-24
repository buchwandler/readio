from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import __version__
from . import api as public_api
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
        help="unambiguous scripting form; read UTF-8 text from a file",
    )
    parser.add_argument(
        "--input-format",
        choices=("auto", "text", "markdown", "ssmd"),
        default="auto",
        help=(
            "input interpretation; auto infers from a resolved file suffix and otherwise "
            "uses text; explicit text disables positional file detection"
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


def _add_synthesis_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--engine",
        help="synthesis backend; default from configuration",
    )
    parser.add_argument("--offline", action="store_true", help="do not fetch engine assets")
    parser.add_argument("--refresh", action="store_true", help="refresh engine discovery metadata")
    parser.add_argument(
        "--voice",
        help="stable selector or canonical backend voice ID, e.g. de-ko-3, de-pi-9, or af_sarah",
    )
    parser.add_argument("--speaker", help="named or numeric speaker for multi-speaker engines")
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
            "short-sentence synthesis strategy: auto uses the PyKokoro default; "
            "off disables handling; wrap uses lightweight phoneme context; "
            "phrase/randomized-phrase use carrier-phrase extraction"
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
    bindings: dict[str, str] = {}
    for value in values:
        if value.count("=") != 1:
            raise ValueError("--voice-bind must use ROLE=VOICE_ID")
        role, voice_id = value.split("=", 1)
        if not role or not voice_id:
            raise ValueError("--voice-bind must use non-empty ROLE=VOICE_ID")
        if role in bindings:
            raise ValueError(f"duplicate --voice-bind role {role!r}")
        bindings[role] = voice_id
    return bindings


def _add_runtime_options(parser: argparse.ArgumentParser, *, playback: bool = True) -> None:
    _add_synthesis_options(parser)
    _add_voice_resolution_options(parser)
    if playback:
        parser.add_argument("--queue-size", type=int, help="audio queue depth")
        parser.add_argument("--device", help="sounddevice output device name or id")


def _add_progress_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=None,
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
    if not progress.enabled:
        return None

    from types import SimpleNamespace

    def handle(event: public_api.ReadioEvent) -> None:
        if event.kind == "stage.started":
            label = event.message or (event.stage or "Working").replace("_", " ").title()
            progress.phase(label)
        elif event.kind == "progress" and event.stage == "synthesis":
            progress.synthesis_event(
                SimpleNamespace(
                    kind=event.message,
                    details=event.details,
                    completed=event.completed,
                    total=event.total,
                    unit_id=event.unit_id,
                    text=event.details.get("text"),
                )
            )
        elif event.kind == "progress" and event.stage == "composition":
            if event.message in {"compose_started", "compose_completed"}:
                progress.composition_event(
                    SimpleNamespace(
                        kind=event.message,
                        completed_items=event.completed,
                        total_items=event.total,
                        target_sample_rate=event.sample_rate or 0,
                        output_frames=event.sample_count,
                        completed_audio_seconds=event.audio_seconds,
                        total_audio_seconds=event.total_audio_seconds,
                        details=event.details,
                    )
                )
            else:
                progress.phase(event.message or "Composing")

    return handle


_KNOWN_DOCUMENT_SUFFIXES = frozenset({".txt", ".ssmd", ".md", ".markdown", ".mdown", ".mkd"})


def _looks_like_path_token(raw: str) -> bool:
    candidate = Path(raw)
    return (
        candidate.suffix.lower() in _KNOWN_DOCUMENT_SUFFIXES
        or "/" in raw
        or "\\" in raw
        or raw.startswith((".", "~"))
    )


def _normalize_positional_input(args: argparse.Namespace) -> None:
    """Convert one unambiguous positional path into ``args.file`` in place."""
    positional = tuple(getattr(args, "text", ()) or ())
    explicit_file = getattr(args, "file", None)

    if explicit_file is not None and positional:
        raise ValueError("provide either positional text/path or --file, not both")

    if explicit_file is not None or not positional:
        return

    if len(positional) != 1:
        return

    if getattr(args, "input_format", "auto") == "text":
        return

    raw = positional[0]
    candidate = Path(raw).expanduser()
    try:
        exists = candidate.exists()
    except OSError as exc:
        raise ValueError(f"cannot inspect positional input path {raw!r}: {exc}") from exc

    if exists:
        if not candidate.is_file():
            raise ValueError(f"positional input path is not a regular file: {candidate}")
        args.file = candidate
        args.text = []
        return

    if _looks_like_path_token(raw):
        raise ValueError(
            f"positional input {raw!r} looks like a file path, but it does not exist; "
            "correct the path or use --input-format text to speak it literally"
        )


def _read_input(args: argparse.Namespace, cfg: public_api.ReadioConfig) -> public_api.Document:
    if getattr(args, "file", None) is not None and getattr(args, "text", None):
        raise ValueError("provide either positional text/path or --file, not both")
    if args.file is not None:
        return public_api.document_from_file(args.file, input_format=args.input_format)
    input_format = args.input_format if args.input_format != "auto" else "text"
    if args.text:
        return public_api.document_from_text(" ".join(args.text), input_format=input_format)
    if sys.stdin.isatty():
        raise ValueError("provide text, --file PATH, or pipe text on stdin")
    return public_api.document_from_text(sys.stdin.read(), input_format=input_format)


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
    result: object,
    cfg: public_api.ReadioConfig,
    synthesis: object | None = None,
) -> dict[str, str]:
    provider = result.provider
    unresolved = getattr(result, "unresolved_voice_references", None)
    if unresolved is None:
        names = set(result.unresolved_references)
        unresolved = tuple(item for item in result.voice_references if item.reference in names)
    resolved_model = getattr(synthesis, "resolved_model", None)
    available = (
        tuple(resolved_model.voices)
        if resolved_model is not None
        else tuple(cfg.voices[provider].ids)
    )
    print(f"SSMD uses {len(unresolved)} unconfigured voice references for provider {provider!r}:")
    print()
    for use in unresolved:
        print(f"  {use.reference} ({use.count} uses)")
    print()
    print("Available voices:")
    for index, voice in enumerate(available, start=1):
        print(f"  {index}. {voice}")
    bindings: dict[str, str] = {}
    for use in unresolved:
        while True:
            choice = input(f"Voice for {use.reference} [enter number or voice ID]: ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(available):
                bindings[use.reference] = available[int(choice) - 1]
                break
            if choice in available:
                bindings[use.reference] = choice
                break
            print(f"unknown voice {choice!r}; choose a number or configured voice ID")
    print()
    print("Using for this render:")
    for role, voice in bindings.items():
        print(f"  {role} -> {voice}")
    return bindings


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
    return public_api.SynthesisRequest(
        language=getattr(args, "lang", None) or default_language,
        engine=getattr(args, "engine", None),
        model=getattr(args, "model", None),
        model_source=getattr(args, "model_source", None),
        quality=getattr(args, "quality", None),
        voice=getattr(args, "voice", None),
        lexicons=tuple(args.lexicons) if getattr(args, "lexicons", None) is not None else None,
        speaker=getattr(args, "speaker", None),
        clear_lexicons=bool(getattr(args, "no_lexicons", False)),
        auto_lexicons=bool(getattr(args, "auto_lexicons", False)),
        g2p_fallback=getattr(args, "g2p_fallback", None),
        spacy=getattr(args, "spacy", None),
        short_sentence=getattr(args, "short_sentence", None),
        lexicon_data_policy=getattr(args, "lexicon_data_policy", None),
        language_detection=getattr(args, "language_detection", None),
        detect_languages=(
            tuple(args.detect_languages)
            if getattr(args, "detect_languages", None) is not None
            else None
        ),
        allow_experimental=bool(getattr(args, "allow_experimental", False)),
        speed=getattr(args, "speed", None),
        pause_mode=getattr(args, "pause_mode", None),
        unit=getattr(args, "unit", None),
        offline=bool(getattr(args, "offline", False)),
        refresh=bool(getattr(args, "refresh", False)),
    )


def _build_plan_request(
    args: argparse.Namespace,
    app: public_api.Readio,
    *,
    operation: str = "render",
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
            bindings.update(_prompt_for_missing_voices(result, cfg))

    synthesis = _synthesis_request_from_args(args)

    output = public_api.OutputRequest(
        mode="file" if operation == "render" else "playback",
        requested_format=getattr(args, "format", None),
        requested_path=getattr(args, "output", None),
        force=bool(getattr(args, "force", False)),
    )

    return public_api.PlanRequest(
        operation=operation,  # type: ignore[arg-type]
        input=public_api.InputRequest(
            document=document,
            requested_format=getattr(args, "input_format", "auto"),
            selector=getattr(args, "select", "all"),
            source_kind=(
                "file"
                if getattr(args, "file", None) is not None
                else "literal"
                if getattr(args, "text", None)
                else "stdin"
            ),
        ),
        synthesis=synthesis,
        output=output,
        voice_bindings=bindings,
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
        composition=public_api.CompositionOptions(target_lufs=getattr(args, "target_lufs", None)),
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


def _cmd_compose(args: argparse.Namespace) -> int:
    app = _api_for(args)
    progress = _build_progress(args)
    with progress:
        result = app.projects.compose(
            args.project or Path.cwd(),
            public_api.CompositionOptions(
                target_lufs=args.target_lufs,
                true_peak_ceiling_dbtp=args.true_peak_ceiling_dbtp,
                peak_policy=args.peak_policy,
                clip_policy=args.clip_policy,
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
                },
                ensure_ascii=False,
            )
        )
    else:
        print(f"Composition: {result.composition_id}")
        if result.master_path is not None:
            print(f"Master: {result.master_path}")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    result = _api_for(args).projects.export(
        args.project or Path.cwd(),
        public_api.ExportOptions(format=args.format, bitrate=args.bitrate, output=args.output),
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
        composition=public_api.CompositionOptions(target_lufs=args.target_lufs),
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
        for operation in result.operations:
            print(f"{operation.stage}: {operation.action}")
    return 0


def _cmd_project(args: argparse.Namespace) -> int:
    if args.project_command != "init":
        raise ValueError(f"unknown project command: {args.project_command}")
    project = _api_for(args).projects.create(args.source, output=args.output)
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, **project.to_dict()}, ensure_ascii=False))
    else:
        print(project.root)
    return 0


def _audiobook_chapter_json(chapter: public_api.AudiobookChapter) -> dict[str, object]:
    return {
        "number": chapter.number,
        "source_id": chapter.source_id,
        "title": chapter.title,
        "href": chapter.href,
        "parent_id": chapter.parent_id,
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
        args.source, chapters=args.chapters, output=args.output
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
        for row in result.stages:
            reusable = row.details.get("reusable")
            total = row.details.get("total")
            details = f" ({reusable}/{total} units reusable)" if reusable is not None else ""
            if row.blocked_by:
                details += f" blocked by {row.blocked_by}"
            print(f"{row.stage.upper():<12} {row.state:<7} {row.reason}{details}")
        print()
        if result.next_actions:
            stage = result.next_actions[0].stage
            commands = {
                "plan": "readio plan build",
                "synthesis": "readio synth",
                "composition": "readio compose",
                "export": "readio export",
            }
            print("Next:")
            print(f"  {commands[stage]}")
        else:
            print("Project is fully built.")
    return 0


def _cmd_plan_build(args: argparse.Namespace) -> int:
    app = _api_for(args)
    project_path = getattr(args, "project", None) or Path.cwd()
    result = app.projects.plan(project_path)
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
    project = app.projects.open(getattr(args, "project", None) or Path.cwd())
    inspection = app.roles.inspect_project(project, provider=getattr(args, "provider", None))
    result = {"ok": True, "project": str(project.root), **inspection.to_dict()}
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False))
        return 0
    print(f"Project:  {project.name}")
    print(f"Provider: {inspection.provider}")
    print(f"SSMD roles: {len(inspection.roles)}")
    print()
    print(f"{'ROLE':<12} {'USES':>4}  {'VOICE':<12} SOURCE")
    print(f"{'-' * 12} {'-' * 4}  {'-' * 12} {'-' * 10}")
    for role in inspection.roles:
        voice = role.effective_voice or "-"
        source = _project_role_source(role)
        print(f"{role.role:<12} {role.uses:>4}  {voice:<12} {source}")
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


def _cmd_plan_bind(args: argparse.Namespace) -> int:
    app = _api_for(args)
    project = args.project or Path.cwd()
    result = app.roles.bind_project(
        project,
        args.role,
        args.voice,
        provider=args.provider,
        discovery=public_api.DiscoveryOptions(
            offline=bool(args.offline), refresh=bool(args.refresh)
        ),
    )
    project_ref = app.projects.open(project)
    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "ok": True,
                    "project": str(project_ref.root),
                    "role": result.role,
                    "stored_voice": result.project_binding,
                },
                ensure_ascii=False,
            )
        )
    else:
        print(f"Project: {project_ref.root.name}")
        print(f"{result.role} -> {result.project_binding}")
        print("Source: project")
        print()
        print("Semantic plan unchanged.")
        print("Active synthesis must be refreshed if one exists.")
    return 0


def _cmd_plan_unbind(args: argparse.Namespace) -> int:
    app = _api_for(args)
    project = args.project or Path.cwd()
    before = app.roles.inspect_project(project, provider=args.provider)
    removed = next((item for item in before.roles if item.role == args.role), None)
    app.roles.unbind_project(project, args.role, provider=args.provider)
    after = app.roles.inspect_project(project, provider=args.provider)
    effective = next((item for item in after.roles if item.role == args.role), None)
    result = {
        "ok": True,
        "project": str(app.projects.open(project).root),
        "role": args.role,
        "removed_voice": removed.project_binding if removed is not None else None,
        "effective_voice": effective.effective_voice if effective is not None else None,
        "origin": effective.origin if effective is not None else None,
    }
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("Removed project binding:")
        print(f"  {result['role']} -> {result['removed_voice']}")
        print()
        if result["effective_voice"] is None:
            print(f"Role {result['role']!r} is now unresolved.")
        else:
            print("Effective binding is now:")
            origin = result["origin"]
            if origin == "config.voice_role":
                origin = "config"
            print(f"  {result['role']} -> {result['effective_voice']} ({origin})")
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
                },
                ensure_ascii=False,
            )
        )
    else:
        print(output)


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
                for operation in result.operations:
                    print(f"{operation.stage}: {operation.action}")
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
        except (public_api.ExecutionError, public_api.OutputError) as error:
            if "diagnostics" not in error.details:
                raise
            plan = app.speech.plan(request)
            if getattr(args, "json", False):
                print(json.dumps(plan.to_dict(), ensure_ascii=False, default=str))
            else:
                print(format_plan_human(plan))
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
    result = app.ssmd.check(
        args.file,
        synthesis=synthesis,
        bindings=bindings,
        roundtrip=bool(args.roundtrip),
    )
    if result.analysis.unresolved_references and args.resolve_voices:
        if args.json or not sys.stdin.isatty():
            raise ValueError(
                "--resolve-voices requires an interactive terminal; "
                "provide --voice-bind ROLE=VOICE_ID instead"
            )
        bindings.update(_prompt_for_missing_voices(result.analysis, cfg, synthesis))
        result = app.ssmd.check(
            args.file,
            synthesis=synthesis,
            bindings=bindings,
            roundtrip=bool(args.roundtrip),
        )
    analysis = result.analysis
    if analysis.unresolved_references:
        unresolved = set(analysis.unresolved_references)
        references = tuple(
            item for item in analysis.voice_references if item.reference in unresolved
        )
        available = tuple(
            cfg.voices[analysis.provider].ids if analysis.provider in cfg.voices else ()
        )
        reference = references[0].reference
        header_template = {
            "voice_bindings": {analysis.provider: {item.reference: None for item in references}}
        }
        message = (
            f"cannot resolve {len(references)} SSMD voice reference"
            f"{'s' if len(references) != 1 else ''} for provider {analysis.provider!r}\n"
            + "\n".join(f"  {item.reference} ({item.count} uses)" for item in references)
            + "\n\nAdd document-local bindings:\n  voice_bindings:\n"
            + f"    {analysis.provider}:\n"
            + "".join(f"      {item.reference}: <voice-id>\n" for item in references)
            + "\nRun `readio voices list` to inspect available voices."
        )
        raise public_api.VoiceResolutionError(
            message,
            provider=analysis.provider,
            reference=reference,
            references=references,
            available_voices=available,
            header_template=header_template,
            source_path=result.source_path,
        )
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
    profiles = config.language_profiles()
    resolution = (
        config.resolve_language_profile(args.language) if getattr(args, "language", None) else None
    )
    language = resolution.normalized if resolution is not None else None
    if getattr(args, "offline", False) and getattr(args, "refresh", False):
        raise ValueError("--offline and --refresh cannot be combined")
    if args.defaults_command == "list":
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

    existing = profiles.get(language, public_api.LanguageSettings())
    if args.lexicons is not None:
        lexicons = tuple(args.lexicons)
    elif args.no_lexicons:
        lexicons = ()
    elif args.auto_lexicons:
        lexicons = None
    else:
        lexicons = existing.lexicons
    settings = replace(
        existing,
        model=args.model if args.model is not None else existing.model,
        source=args.model_source if args.model_source is not None else existing.source,
        quality=args.quality if args.quality is not None else existing.quality,
        voice=args.voice if args.voice is not None else existing.voice,
        lexicons=lexicons,
        g2p_fallback=(
            args.g2p_fallback if args.g2p_fallback is not None else existing.g2p_fallback
        ),
        lexicon_data_policy=(
            args.lexicon_data_policy
            if args.lexicon_data_policy is not None
            else existing.lexicon_data_policy
        ),
        allow_experimental=existing.allow_experimental or args.allow_experimental,
    )
    settings = config.set_language_profile(
        language,
        settings,
        validate_runtime=True,
        discovery=public_api.DiscoveryOptions(
            offline=bool(args.offline),
            refresh=bool(args.refresh),
            preference=settings.source or "auto",
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
    payload = dict(entry.to_dict())
    payload["number"] = entry.slot
    payload["backend"] = entry.engine
    payload["selector_status"] = "assigned" if entry.selector else "unassigned"
    return payload


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
    print(f"Selector:       {entry.selector or '-'}")
    print(f"Engine:         {entry.engine}")
    print(f"Slot:           {entry.slot if entry.slot is not None else '-'}")
    print(f"Selector status: {entry.status}")
    print(f"Qualified ID:   {entry.qualified_id}")
    print(f"Voice:          {entry.id}")
    print(f"Gender:         {entry.gender}")
    print(f"Locale:         {entry.locale}")
    print(f"Language:       {entry.language_label}")
    print(f"Model:          {entry.model}")
    print(f"Source:         {entry.source}")
    print(f"Default voice:  {'yes' if entry.default else 'no'}")
    print(f"Status:         {entry.status}")
    print(f"Experimental:   {'yes' if entry.experimental else 'no'}")
    print()
    print("Equivalent selection:")
    print(
        f"  --lang {entry.locale} --model {entry.model} "
        f"--model-source {entry.source} --voice {entry.id}"
    )


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
    available_engines: set[str] | None = None,
) -> tuple[str | None, str | None]:
    aliases = {"kokoro": "pykokoro", "pipersynth": "piper"}
    available = available_engines or {"piper", "pykokoro"}
    if engine is not None:
        return aliases.get(engine.casefold(), engine), model
    if model is not None:
        canonical = aliases.get(model.casefold(), model.casefold())
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
        preference=args.preference,
    )
    if args.voices_command == "list":
        available_engines = {item.id.casefold() for item in app.catalog.engines()}
        engine, model = _normalize_voice_list_filters(
            engine=args.engine, model=args.model, available_engines=available_engines
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
            "SELECTOR  ENGINE    VOICE             GENDER   LOCALE    "
            "LANGUAGE                 MODEL          STATUS"
        )
        print(
            "--------  --------  ----------------  -------  --------  "
            "-----------------------  -------------  ------------"
        )
        for entry in voices:
            print(
                f"{entry.selector or '-':<9} {entry.engine:<9} {entry.id:<17} {entry.gender:<8} "
                f"{entry.locale:<9} {entry.language_label:<24} {entry.model:<14} {entry.status}"
            )
        return 0

    listing = app.catalog.voice_listing(
        args.selector,
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
    provider = args.provider or app.config.ssmd.voice_provider
    if getattr(args, "legacy_roles", False):
        print(
            "Warning: `readio voices roles|bind|unbind` is deprecated; use `readio roles`.",
            file=sys.stderr,
        )
    if args.roles_command == "list":
        bindings = app.roles.list_global(provider=provider)
        roles = {item.role: item.voice for item in bindings}
        result = {"ok": True, "provider": provider, "roles": roles}
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"Provider: {provider}")
            print()
            print("ROLE        VOICE")
            print("----------  ------------")
            for role, voice in roles.items():
                print(f"{role:<11} {voice}")
        return 0
    if args.roles_command == "bind":
        binding = app.roles.bind_global(args.role, args.voice_id, provider=provider)
        result = {
            "ok": True,
            "provider": provider,
            "role": binding.role,
            "voice": binding.voice,
            "path": app.configuration.path(),
        }
        if args.json:
            print(json.dumps(_json_value(result), ensure_ascii=False))
        else:
            print(f"{binding.role} -> {binding.voice} ({provider})")
        return 0
    if args.roles_command == "unbind":
        app.roles.unbind_global(args.role, provider=provider)
        result = {
            "ok": True,
            "provider": provider,
            "role": args.role,
            "path": app.configuration.path(),
        }
        if args.json:
            print(json.dumps(_json_value(result), ensure_ascii=False))
        else:
            print(f"removed {args.role} ({provider})")
        return 0
    raise AssertionError("unreachable")


def _cmd_config(args: argparse.Namespace) -> int:
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
    parser = argparse.ArgumentParser(prog="readio", description="Stream text to PyKokoro TTS")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="show internal diagnostics; repeat (-vv) for debug-level detail",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    speak = sub.add_parser("speak", help="read text aloud")
    _add_input_options(speak)
    _add_runtime_options(speak)
    speak.set_defaults(func=_cmd_speak)

    render = sub.add_parser("render", help="render text to an audio file")
    _add_input_options(render)
    _add_audio_output_options(render)
    render.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output audio path (.wav, .mp3, .m4a, or .ogg)",
    )
    render.add_argument("--target-lufs", type=float, help="project composition loudness target")
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
        help="build a project plan or inspect and bind its SSMD roles",
    )
    plan_cmd.add_argument("--json", action="store_true", help="emit JSON output")
    plan_cmd.set_defaults(func=_cmd_plan_build, project=None)
    plan_sub = plan_cmd.add_subparsers(dest="plan_action")

    plan_build = plan_sub.add_parser("build", help="build semantic plans for a project")
    plan_build.add_argument("project", nargs="?", type=Path)
    plan_build.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    plan_build.set_defaults(func=_cmd_plan_build)

    plan_roles = plan_sub.add_parser("roles", help="inspect SSMD roles and effective voices")
    plan_roles.add_argument("project", nargs="?", type=Path)
    plan_roles.add_argument("--provider", help="voice provider, default from configuration")
    plan_roles.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    plan_roles.set_defaults(func=_cmd_plan_roles)

    plan_bind = plan_sub.add_parser("bind", help="bind a project role to a provider voice")
    plan_bind.add_argument("role")
    plan_bind.add_argument("voice")
    plan_bind.add_argument("--provider", help="voice provider, default from configuration")
    plan_bind.add_argument("--project", type=Path)
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
    plan_unbind.add_argument("--provider", help="voice provider, default from configuration")
    plan_unbind.add_argument("--project", type=Path)
    plan_unbind.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    plan_unbind.set_defaults(func=_cmd_plan_unbind)
    project_cmd = sub.add_parser("project", help="create or maintain a persistent Readio project")
    project_sub = project_cmd.add_subparsers(dest="project_command", required=True)
    project_init = project_sub.add_parser(
        "init", help="initialize a project from a source document"
    )
    project_init.add_argument("source", type=Path)
    project_init.add_argument("-o", "--output", type=Path)
    project_init.add_argument("--json", action="store_true")
    project_cmd.set_defaults(func=_cmd_project)
    audiobook_cmd = sub.add_parser(
        "audiobook", help="inspect EPUB chapters and initialize audiobook projects"
    )
    audiobook_sub = audiobook_cmd.add_subparsers(dest="audiobook_command", required=True)
    audiobook_chapters = audiobook_sub.add_parser("chapters", help="list selectable EPUB chapters")
    audiobook_chapters.add_argument("source", type=Path)
    audiobook_chapters.add_argument("--json", action="store_true")
    audiobook_chapters.set_defaults(func=_cmd_audiobook_chapters)
    audiobook_init = audiobook_sub.add_parser(
        "init", help="initialize a chapter-scoped project from an EPUB"
    )
    audiobook_init.add_argument("source", type=Path)
    audiobook_init.add_argument("--chapters", default="all")
    audiobook_init.add_argument("-o", "--output", type=Path)
    audiobook_init.add_argument("--json", action="store_true")
    audiobook_init.set_defaults(func=_cmd_audiobook_init)

    status_cmd = sub.add_parser("status", help="show persistent project stage freshness")
    status_cmd.add_argument("project", nargs="?", type=Path)
    status_cmd.add_argument("--json", action="store_true")
    status_cmd.set_defaults(func=_cmd_status)

    synth_cmd = sub.add_parser("synth", help="incrementally synthesize a Readio project")
    synth_cmd.add_argument("project", nargs="?", type=Path)
    synth_cmd.add_argument("--select", default="all")
    _add_synthesis_options(synth_cmd)
    _add_voice_resolution_options(synth_cmd)
    _add_progress_option(synth_cmd)
    synth_cmd.add_argument("--json", action="store_true")
    synth_cmd.set_defaults(func=_cmd_synth)

    compose_cmd = sub.add_parser("compose", help="compose persisted project synthesis audio")
    compose_cmd.add_argument("project", nargs="?", type=Path)
    compose_cmd.add_argument("--target-lufs", type=float)
    compose_cmd.add_argument("--true-peak-ceiling-dbtp", type=float, default=-1.0)
    compose_cmd.add_argument(
        "--peak-policy", choices=("reduce_gain", "error"), default="reduce_gain"
    )
    compose_cmd.add_argument("--clip-policy", choices=("clamp", "warn", "error"), default="clamp")
    _add_progress_option(compose_cmd)
    compose_cmd.add_argument("--json", action="store_true")
    compose_cmd.set_defaults(func=_cmd_compose)

    export_cmd = sub.add_parser("export", help="encode a composed project master")
    export_cmd.add_argument("project", nargs="?", type=Path)
    export_cmd.add_argument("--format", choices=public_api.SUPPORTED_AUDIO_FORMATS, default="wav")
    export_cmd.add_argument("--bitrate")
    export_cmd.add_argument("-o", "--output", type=Path)
    export_cmd.add_argument("--json", action="store_true")
    export_cmd.set_defaults(func=_cmd_export)

    preview_cmd = sub.add_parser("preview", help="synthesize and compose a selected project range")
    preview_cmd.add_argument("project", nargs="?", type=Path)
    preview_cmd.add_argument("--select", default="first:3")
    preview_cmd.add_argument("-o", "--output", type=Path)
    preview_cmd.add_argument("--activate", action="store_true")
    _add_synthesis_options(preview_cmd)
    _add_voice_resolution_options(preview_cmd)
    _add_progress_option(preview_cmd)
    preview_cmd.add_argument("--json", action="store_true")
    preview_cmd.set_defaults(func=_cmd_preview)
    from .spotify_cli import add_spotify_parser

    add_spotify_parser(sub)

    models = sub.add_parser("models", help="discover PyKokoro runtime models")
    models_sub = models.add_subparsers(dest="models_command", required=True)
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

    lexicons = sub.add_parser("lexicons", help="discover named synthesis lexicons")
    lexicons_sub = lexicons.add_subparsers(dest="lexicons_command", required=True)
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

    defaults = sub.add_parser("defaults", help="manage per-language synthesis defaults")
    defaults_sub = defaults.add_subparsers(dest="defaults_command", required=True)
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
    defaults_set.add_argument("--allow-experimental", action="store_true")
    defaults_set.add_argument("--offline", action="store_true")
    defaults_set.add_argument("--refresh", action="store_true")
    defaults_set.add_argument("--json", action="store_true")
    defaults_set.set_defaults(func=_cmd_defaults)
    defaults_reset = defaults_sub.add_parser("reset", help="remove a language default")
    defaults_reset.add_argument("language")
    defaults_reset.add_argument("--json", action="store_true")
    defaults_reset.set_defaults(func=_cmd_defaults)

    voices = sub.add_parser("voices", help="discover runnable voices and short selectors")
    voices_sub = voices.add_subparsers(dest="voices_command", required=True)
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
        help="filter by synthesis backend or system; takes precedence over engine-name model shortcuts",
    )
    voices_list.add_argument("--offline", action="store_true")
    voices_list.add_argument("--refresh", action="store_true")
    voices_list.add_argument(
        "--preference", choices=("auto", "github", "huggingface", "upstream"), default="auto"
    )
    voices_list.add_argument("--json", action="store_true")
    voices_list.set_defaults(func=_cmd_voices)
    voices_show = voices_sub.add_parser("show", help="show one voice selector or canonical ID")
    voices_show.add_argument("selector")
    voices_show.add_argument("--engine", help="filter by synthesis backend")
    voices_show.add_argument("--offline", action="store_true")
    voices_show.add_argument("--refresh", action="store_true")
    voices_show.add_argument(
        "--preference", choices=("auto", "github", "huggingface", "upstream"), default="auto"
    )
    voices_show.add_argument("--json", action="store_true")
    voices_show.set_defaults(func=_cmd_voices)

    roles = sub.add_parser("roles", help="manage persistent SSMD role bindings")
    roles_sub = roles.add_subparsers(dest="roles_command", required=True)
    roles_list = roles_sub.add_parser("list", help="list configured logical roles")
    roles_list.add_argument("--provider")
    roles_list.add_argument("--json", action="store_true")
    roles_list.set_defaults(func=_cmd_roles)
    roles_bind = roles_sub.add_parser("bind", help="persist a logical role binding")
    roles_bind.add_argument("role")
    roles_bind.add_argument("voice_id")
    roles_bind.add_argument("--provider")
    roles_bind.add_argument("--json", action="store_true")
    roles_bind.set_defaults(func=_cmd_roles)
    roles_unbind = roles_sub.add_parser("unbind", help="remove a logical role binding")
    roles_unbind.add_argument("role")
    roles_unbind.add_argument("--provider")
    roles_unbind.add_argument("--json", action="store_true")
    roles_unbind.set_defaults(func=_cmd_roles)

    legacy_roles = voices_sub.add_parser("roles", help="deprecated alias for readio roles list")
    legacy_roles.add_argument("--provider")
    legacy_roles.add_argument("--json", action="store_true")
    legacy_roles.set_defaults(func=_cmd_roles, roles_command="list", legacy_roles=True)
    legacy_bind = voices_sub.add_parser("bind", help="deprecated alias for readio roles bind")
    legacy_bind.add_argument("role")
    legacy_bind.add_argument("voice_id")
    legacy_bind.add_argument("--provider")
    legacy_bind.add_argument("--json", action="store_true")
    legacy_bind.set_defaults(func=_cmd_roles, roles_command="bind", legacy_roles=True)
    legacy_unbind = voices_sub.add_parser("unbind", help="deprecated alias for readio roles unbind")
    legacy_unbind.add_argument("role")
    legacy_unbind.add_argument("--provider")
    legacy_unbind.add_argument("--json", action="store_true")
    legacy_unbind.set_defaults(func=_cmd_roles, roles_command="unbind", legacy_roles=True)

    ssmd = sub.add_parser("ssmd", help="inspect SSMD documents")
    ssmd_sub = ssmd.add_subparsers(dest="ssmd_command", required=True)
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

    cfg = sub.add_parser("config", help="manage persistent configuration")
    cfg_sub = cfg.add_subparsers(dest="config_command", required=True)
    cfg_sub.add_parser("path", help="print config path")
    cfg_sub.add_parser("show", help="show effective persisted config as JSON")
    init = cfg_sub.add_parser("init", help="write the default config")
    init.add_argument("--force", action="store_true")
    cfg_sub.add_parser("validate", help="validate the effective configuration")
    set_cmd = cfg_sub.add_parser("set", help="set one dotted config key")
    set_cmd.add_argument("key")
    set_cmd.add_argument("value")
    cfg.set_defaults(func=_cmd_config)

    template = sub.add_parser("template", help="manage user templates")
    template_sub = template.add_subparsers(dest="template_command", required=True)
    path_cmd = template_sub.add_parser("path")
    path_cmd.add_argument("name", nargs="?")
    template_sub.add_parser("list")
    validate_template = template_sub.add_parser("validate")
    validate_template.add_argument("name", nargs="?")
    validate_template.add_argument("--all", action="store_true")
    validate_template.add_argument("--roundtrip", action="store_true")
    validate_template.add_argument("--json", action="store_true")
    show_cmd = template_sub.add_parser("show")
    show_cmd.add_argument("name")
    add_cmd = template_sub.add_parser("add")
    add_cmd.add_argument("name")
    add_cmd.add_argument("--file", type=Path)
    add_cmd.add_argument("--force", action="store_true")
    remove_cmd = template_sub.add_parser("remove")
    remove_cmd.add_argument("name")
    reset_cmd = template_sub.add_parser("reset")
    reset_cmd.add_argument("name", nargs="?")
    reset_cmd.add_argument("--all", action="store_true")
    use_cmd = template_sub.add_parser("use")
    use_cmd.add_argument("name_template")
    use_cmd.add_argument("--name")
    template.set_defaults(func=_cmd_template)

    ingest = sub.add_parser("ingest", help="manage ingest files")
    ingest_sub = ingest.add_subparsers(dest="ingest_command", required=True)
    ingest_sub.add_parser("path")
    new_cmd = ingest_sub.add_parser("new")
    new_cmd.add_argument("--name")
    new_cmd.add_argument("--template")
    ingest_sub.add_parser("list")
    ingest.set_defaults(func=_cmd_ingest)

    doctor = sub.add_parser("doctor", help="check runtime dependencies and storage")
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
