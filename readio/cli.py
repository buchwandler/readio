from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from . import __version__
from .audio import RenderSummary
from .config import (
    ReadioConfig,
    bind_voice_role,
    config_path,
    default_config,
    load_config,
    provider_role_map,
    provider_voices,
    save_config,
    set_config_value,
    unbind_voice_role,
    validate_config,
    with_overrides,
)
from .document import InputDocument, document_from_file, document_from_stdin, document_from_text
from .errors import ReadioError, RenderError
from .formats import (
    SUPPORTED_AUDIO_FORMATS,
    AudioFormat,
    audio_format_diagnostics,
    ensure_audio_format_available,
    format_suffix,
    normalize_audio_output_path,
    resolve_audio_format,
)
from .ingest import list_ingest, new_ingest
from .paths import resolve_render_output
from .reader import SelectionError, render_live, render_text, speak_live, speak_text
from .spotify import (
    SpotifyError,
    build_timeline,
    set_timeline,
    upload_episode,
    wait_for_episode,
)
from .ssmd import analyze_ssmd, preflight_ssmd
from .ssmd_authoring import materialize_voice_bindings, roundtrip_check
from .templates import (
    add_template,
    list_templates,
    packaged_template_names,
    remove_template,
    reset_template,
    seed_templates,
    show_template,
    template_path,
)
from .wave import atomic_audio_path, create_audio_sink


def _add_input_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("text", nargs="*", help="literal text; omit to read stdin")
    parser.add_argument("--file", type=Path, help="read UTF-8 text from a file")
    parser.add_argument(
        "--input-format",
        choices=("auto", "text", "markdown", "ssmd"),
        default="auto",
        help="input interpretation; auto uses the file suffix and otherwise defaults to text",
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
        choices=SUPPORTED_AUDIO_FORMATS,
        help=(
            "audio output format; inferred from --output when possible; "
            "default: wav"
        ),
    )


def _add_synthesis_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--voice", help="PyKokoro voice, e.g. af_sarah")
    parser.add_argument("--lang", help="language code, e.g. en-us, de, fr")
    parser.add_argument("--speed", type=float, help="speech speed multiplier")
    parser.add_argument("--pause-mode", choices=("tts", "manual", "auto"))
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

def _resolved_config(args: argparse.Namespace) -> ReadioConfig:
    return with_overrides(
        load_config(),
        voice=getattr(args, "voice", None),
        lang=getattr(args, "lang", None),
        speed=getattr(args, "speed", None),
        pause_mode=getattr(args, "pause_mode", None),
        unit=getattr(args, "unit", None),
        queue_size=getattr(args, "queue_size", None),
        device=getattr(args, "device", None),
    )


def _read_input(args: argparse.Namespace, cfg: ReadioConfig) -> InputDocument:
    if args.file is not None:
        return document_from_file(args.file, input_format=args.input_format)
    input_format = args.input_format if args.input_format != "auto" else "text"
    if args.text:
        return document_from_text(" ".join(args.text), input_format=input_format)
    if sys.stdin.isatty():
        raise ValueError("provide text, --file PATH, or pipe text on stdin")
    return document_from_stdin(sys.stdin.read(), input_format=input_format)


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


def _prompt_for_missing_voices(result: object, cfg: ReadioConfig) -> dict[str, str]:
    provider = result.provider
    available = tuple(cfg.voices[provider].ids)
    print(
        f"SSMD uses {len(result.unresolved_voice_references)} unconfigured voice references "
        f"for provider {provider!r}:"
    )
    print()
    for use in result.unresolved_voice_references:
        print(f"  {use.reference} ({use.count} uses)")
    print()
    print("Available voices:")
    for index, voice in enumerate(available, start=1):
        print(f"  {index}. {voice}")
    bindings: dict[str, str] = {}
    for use in result.unresolved_voice_references:
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


def _prepared_input(
    args: argparse.Namespace,
    cfg: ReadioConfig,
) -> tuple[InputDocument, dict[str, str]]:
    bindings = _parse_voice_bindings(getattr(args, "voice_bind", []))
    document = _read_input(args, cfg)
    if document.format != "ssmd":
        return document, bindings
    result = analyze_ssmd(
        document.text,
        cfg,
        source_path=document.source_path,
        additional_bindings=bindings,
    )
    if result.unresolved_voice_references and getattr(args, "resolve_voices", False):
        if getattr(args, "json", False) or not sys.stdin.isatty():
            raise ValueError(
                "--resolve-voices requires an interactive terminal; "
                "provide --voice-bind ROLE=VOICE_ID instead"
            )
        bindings.update(_prompt_for_missing_voices(result, cfg))
    preflight_ssmd(
        document.text,
        cfg,
        source_path=document.source_path,
        additional_bindings=bindings,
    )
    return document, bindings


def _cmd_speak(args: argparse.Namespace) -> int:
    cfg = _resolved_config(args)
    if args.live:
        _validate_live(args)
        speak_live(sys.stdin, cfg.reader, unit=args.unit)
    else:
        document, bindings = _prepared_input(args, cfg)
        speak_text(
            document,
            cfg,
            selector=args.select,
            unit=args.unit,
            ssmd_voice_bindings=bindings,
        )
    return 0


def _render_audio(
    args: argparse.Namespace,
    path: Path,
    *,
    audio_format: AudioFormat,
    cfg: ReadioConfig | None = None,
    document: InputDocument | None = None,
    bindings: Mapping[str, str] | None = None,
) -> RenderSummary:
    cfg = cfg or getattr(args, "_prepared_cfg", None) or _resolved_config(args)
    document = document or getattr(args, "_prepared_document", None)
    if bindings is None:
        bindings = getattr(args, "_prepared_bindings", None)
    with create_audio_sink(path, audio_format) as sink:
        if args.live:
            summary = render_live(sys.stdin, cfg, sink, unit=args.unit)
        else:
            document = document or _prepared_input(args, cfg)[0]
            summary = render_text(
                document,
                cfg,
                sink,
                selector=args.select,
                unit=args.unit,
                ssmd_voice_bindings=bindings or {},
            )
    if summary.sample_count <= 0:
        raise RenderError("render produced no audio")
    return summary


def _cmd_render(args: argparse.Namespace) -> int:
    if args.live:
        _validate_live(args)
    cfg = _resolved_config(args)
    audio_format = resolve_audio_format(requested=args.format, output=args.output)
    output = resolve_render_output(
        cfg,
        explicit=args.output,
        input_path=args.file,
        audio_format=audio_format,
    )
    output = normalize_audio_output_path(output, audio_format)
    ensure_audio_format_available(audio_format)
    prepared = None if args.live else _prepared_input(args, cfg)
    output.parent.mkdir(parents=True, exist_ok=True)
    args._prepared_cfg = cfg
    if prepared:
        args._prepared_document, args._prepared_bindings = prepared
    with atomic_audio_path(output, force=args.force) as temporary:
        _render_audio(args, temporary, audio_format=audio_format)
    print(output)
    return 0


def _cmd_spotify(args: argparse.Namespace) -> int:
    if args.wait_timeout is not None and not (args.wait or args.chapters_from_markers):
        raise ValueError("--wait-timeout requires --wait")
    if args.live:
        _validate_live(args)
    cfg = _resolved_config(args)
    audio_format = resolve_audio_format(requested=args.format, output=args.output)
    ensure_audio_format_available(audio_format)
    prepared = None if args.live else _prepared_input(args, cfg)
    args._prepared_cfg = cfg
    if prepared:
        args._prepared_document, args._prepared_bindings = prepared


    temporary: Path | None = None
    if args.output is None:
        fd, name = tempfile.mkstemp(prefix="readio-", suffix=format_suffix(audio_format))
        os.close(fd)
        temporary = Path(name)
        audio_path = temporary
    else:
        audio_path = normalize_audio_output_path(args.output.expanduser(), audio_format)

    try:
        if args.output is None:
            summary = _render_audio(args, audio_path, audio_format=audio_format)
        else:
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            with atomic_audio_path(audio_path, force=args.force) as target:
                summary = _render_audio(args, target, audio_format=audio_format)
        timeline = (
            build_timeline(summary.markers, summary.sample_rate)
            if args.chapters_from_markers
            else None
        )
        uploaded = upload_episode(
            audio_path,
            title=args.title,
            show_id=args.show_id,
            new_show=args.new_show,
            summary=args.summary,
            image=args.image,
            language=args.language,
        )
        readiness = None
        if args.wait or args.chapters_from_markers:
            readiness = wait_for_episode(uploaded.episode_uri, timeout=args.wait_timeout)
        if timeline is not None:
            if readiness is None or readiness.readiness != "READY":
                raise SpotifyError("episode is not READY; cannot set chapter timeline")
            timeline_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", prefix="readio-timeline-", delete=False
                ) as timeline_file:
                    json.dump(timeline, timeline_file)
                    timeline_path = Path(timeline_file.name)
                set_timeline(uploaded.episode_uri, timeline_path)
            finally:
                if timeline_path is not None:
                    timeline_path.unlink(missing_ok=True)
        result = {
            "ok": True,
            "episode_uri": uploaded.episode_uri,
            "upload_status": uploaded.status,
            "readiness": readiness.readiness if readiness is not None else None,
            "audio_path": str(audio_path) if args.output is not None else None,
            "audio_format": audio_format,
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(uploaded.episode_uri)
            if readiness is not None:
                print(readiness.readiness)
        return 0
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _json_value(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return _json_value({key: getattr(value, key) for key in value.__dataclass_fields__})
    return value


def _cmd_ssmd(args: argparse.Namespace) -> int:
    cfg = load_config()
    if args.ssmd_command == "bind":
        provider = args.provider or cfg.ssmd.voice_provider
        bindings = _parse_voice_bindings(args.voice_bind)
        target = materialize_voice_bindings(
            args.file,
            bindings,
            provider=provider,
            output=args.output,
            in_place=args.in_place,
        )
        print(target)
        return 0
    document = document_from_file(args.file)
    bindings = _parse_voice_bindings(getattr(args, "voice_bind", []))
    analysis = analyze_ssmd(
        document.text,
        cfg,
        source_path=document.source_path,
        additional_bindings=bindings,
    )
    if analysis.unresolved_voice_references and args.resolve_voices:
        if args.json or not sys.stdin.isatty():
            raise ValueError(
                "--resolve-voices requires an interactive terminal; "
                "provide --voice-bind ROLE=VOICE_ID instead"
            )
        bindings.update(_prompt_for_missing_voices(analysis, cfg))
    consumer = preflight_ssmd(
        document.text,
        cfg,
        source_path=document.source_path,
        additional_bindings=bindings,
    )
    result: dict[str, object] = {
        "ok": consumer.ok,
        "source": str(document.source_path),
        "provider": consumer.provider,
        "consumer": {
            "ok": consumer.ok,
            "unresolved": list(consumer.unresolved_references),
            "references": [
                {"name": item.reference, "count": item.count, "lines": list(item.lines)}
                for item in consumer.voice_references
            ],
            "diagnostics": [item.to_dict() for item in consumer.diagnostics],
        },
        "bindings": {
            "document": dict(consumer.document_bindings),
            "defaults": dict(consumer.default_bindings),
        },
        "roundtrip": None,
    }
    if args.roundtrip:
        result["roundtrip"] = _json_value(roundtrip_check(document.source_path, cfg))
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"Source: {document.source_path}")
        print(f"Provider: {consumer.provider}")
        print(f"Document bindings: {dict(consumer.document_bindings)}")
        print(f"Readio defaults: {dict(consumer.default_bindings)}")
        print("Consumer: OK")
        if args.roundtrip:
            print("Roundtrip: OK")
    return 0


def _cmd_voices(args: argparse.Namespace) -> int:
    cfg = load_config()
    provider = args.provider or cfg.ssmd.voice_provider
    settings = cfg.voices.get(provider)
    if settings is None:
        raise ValueError(f"voice provider {provider!r} is not configured")

    if args.voices_command == "list":
        reverse = provider_role_map(cfg, provider)
        voices = [
            {"id": voice, "roles": list(reverse.get(voice, ())) }
            for voice in provider_voices(cfg, provider)
        ]
        result = {"ok": True, "provider": provider, "voices": voices}
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"Provider: {provider}")
            print()
            print("VOICE ID     ROLES")
            for item in voices:
                roles = ", ".join(item["roles"]) or "-"
                print(f"{item['id']:<12} {roles}")
        return 0

    if args.voices_command == "roles":
        result = {"ok": True, "provider": provider, "roles": dict(settings.roles)}
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            for role, voice in sorted(settings.roles.items()):
                print(f"{role} -> {voice}")
        return 0

    if args.voices_command == "bind":
        updated = bind_voice_role(cfg, args.role, args.voice_id, provider)
        path = save_config(updated)
        result = {"ok": True, "provider": provider, "role": args.role, "voice": args.voice_id, "path": path}
        if args.json:
            print(json.dumps(_json_value(result), ensure_ascii=False))
        else:
            print(f"{args.role} -> {args.voice_id} ({provider})")
        return 0

    if args.voices_command == "unbind":
        updated = unbind_voice_role(cfg, args.role, provider)
        path = save_config(updated)
        result = {"ok": True, "provider": provider, "role": args.role, "path": path}
        if args.json:
            print(json.dumps(_json_value(result), ensure_ascii=False))
        else:
            print(f"removed {args.role} ({provider})")
        return 0

    raise AssertionError("unreachable")


def _cmd_config(args: argparse.Namespace) -> int:
    path = config_path()
    if args.config_command == "path":
        print(path)
        return 0
    if args.config_command == "show":
        print(json.dumps(_json_value(load_config(path)), indent=2, ensure_ascii=False))
        return 0
    if args.config_command == "init":
        if path.exists() and not args.force:
            raise ValueError(f"config already exists: {path}; use --force to replace it")
        cfg = default_config()
        save_config(cfg, path)
        cfg.paths.templates.mkdir(parents=True, exist_ok=True)
        cfg.paths.ingest.mkdir(parents=True, exist_ok=True)
        cfg.paths.output.mkdir(parents=True, exist_ok=True)
        seed_templates(cfg.paths.templates, overwrite=False)
        print(path)
        return 0
    if args.config_command == "validate":
        cfg = load_config(path)
        validate_config(cfg)
        print(json.dumps({"ok": True, "paths": _json_value(cfg.paths)}, ensure_ascii=False))
        return 0
    if args.config_command == "set":
        cfg = set_config_value(load_config(path), args.key, args.value)
        save_config(cfg, path)
        print(path)
        return 0
    raise AssertionError("unreachable")


def _validate_template(path: Path, cfg: ReadioConfig, *, roundtrip: bool) -> dict[str, object]:
    try:
        document = document_from_file(path)
        consumer = preflight_ssmd(document.text, cfg, source_path=path)
        result: dict[str, object] = {
            "name": path.stem,
            "source": str(path),
            "ok": consumer.ok,
            "provider": consumer.provider,
            "consumer": {
                "ok": consumer.ok,
                "unresolved": list(consumer.unresolved_references),
                "diagnostics": [item.to_dict() for item in consumer.diagnostics],
            },
            "bindings": {
                "document": dict(consumer.document_bindings),
                "defaults": dict(consumer.default_bindings),
            },
            "roundtrip": None,
        }
        if roundtrip:
            result["roundtrip"] = _json_value(roundtrip_check(path, cfg))
        return result
    except ReadioError as exc:
        return {
            "name": path.stem,
            "source": str(path),
            "ok": False,
            "error": _error_payload(exc),
            "roundtrip": None,
        }


def _cmd_template(args: argparse.Namespace) -> int:
    cfg = load_config()
    directory = cfg.paths.templates
    if args.template_command == "validate":
        if args.all:
            names = list_templates(directory)
        elif args.name is not None:
            names = [Path(args.name).stem]
        else:
            raise ValueError("template validate requires NAME or --all")
        results = [
            _validate_template(template_path(directory, name), cfg, roundtrip=args.roundtrip)
            for name in names
        ]
        result = {"ok": all(item["ok"] for item in results), "templates": results}
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            for item in results:
                status = "OK" if item["ok"] else "FAILED"
                print(f"{item['name']}: {status}")
        return 0 if result["ok"] else 2

    if args.template_command == "path":
        print(template_path(directory, args.name) if args.name else directory)
    elif args.template_command == "list":
        for name in list_templates(directory):
            print(name)
    elif args.template_command == "show":
        print(show_template(directory, args.name), end="")
    elif args.template_command == "add":
        source = Path(args.file) if args.file else None
        content = sys.stdin.read() if source is None and not sys.stdin.isatty() else None
        print(add_template(directory, args.name, source, content=content, force=args.force))
    elif args.template_command == "remove":
        remove_template(directory, args.name)
    elif args.template_command == "reset":
        if args.all:
            for name in packaged_template_names():
                reset_template(directory, name)
        else:
            if args.name is None:
                raise ValueError("template reset requires NAME or --all")
            reset_template(directory, args.name)
    elif args.template_command == "use":
        target = new_ingest(
            cfg.paths.ingest,
            name=args.name,
            template_directory=directory,
            template=args.name_template,
        )
        print(target)
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    cfg = load_config()
    if args.ingest_command == "path":
        print(cfg.paths.ingest)
    elif args.ingest_command == "new":
        target = new_ingest(
            cfg.paths.ingest,
            name=args.name,
            template_directory=cfg.paths.templates,
            template=args.template,
        )
        print(target)
    elif args.ingest_command == "list":
        for path in list_ingest(cfg.paths.ingest):
            print(path.name)
    return 0


def _cmd_doctor(_: argparse.Namespace) -> int:
    cfg = load_config()
    provider = cfg.ssmd.voice_provider
    settings = cfg.voices.get(provider)
    try:
        import pykokoro

        pykokoro_check = getattr(pykokoro, "__version__", "installed")
    except (ImportError, OSError) as exc:  # pragma: no cover - environment dependent
        pykokoro_check = f"ERROR: {exc}"
    try:
        import ssmd

        ssmd_module = getattr(ssmd, "__version__", "installed")
    except (ImportError, OSError) as exc:  # pragma: no cover - environment dependent
        ssmd_module = f"ERROR: {exc}"
    try:
        import sounddevice as sd

        sounddevice_check = getattr(sd, "__version__", "installed")
    except (ImportError, OSError) as exc:  # pragma: no cover - environment dependent
        sounddevice_check = f"ERROR: {exc}"
    try:
        import soundfile as sf

        soundfile_check = getattr(sf, "__libsndfile_version__", "installed")
    except (ImportError, OSError) as exc:  # pragma: no cover - environment dependent
        soundfile_check = f"ERROR: {exc}"
    try:
        preflight_ssmd('<div voice="narrator">health check.</div>', cfg)
        consumer_preflight = "ok"
    except (ReadioError, ImportError, OSError, TypeError, ValueError) as exc:
        consumer_preflight = f"ERROR: {exc}"
    try:
        template_names = list_templates(cfg.paths.templates)
    except ValueError:
        template_names = []

    result = {
        "readio": __version__,
        "config": {"path": str(config_path()), "exists": config_path().exists()},
        "paths": {
            name: {"path": str(path), "exists": path.exists()}
            for name, path in {
                "templates": cfg.paths.templates,
                "ingest": cfg.paths.ingest,
                "output": cfg.paths.output,
            }.items()
        },
        "pykokoro": pykokoro_check,
        "ssmd": {
            "python_api": "ok" if not ssmd_module.startswith("ERROR:") else ssmd_module,
            "module_version": ssmd_module,
            "consumer_preflight": consumer_preflight,
            "executable": shutil.which("ssmd") or "not found",
            "voice_provider": provider,
        },
        "voices": {
            "provider": provider,
            "configured_ids": len(settings.ids) if settings else 0,
            "roles": dict(settings.roles) if settings else {},
            "invalid_roles": [
                role for role, voice in settings.roles.items() if voice not in settings.ids
            ]
            if settings
            else [],
        },
        "sounddevice": sounddevice_check,
        "soundfile": soundfile_check,
        "audio_formats": audio_format_diagnostics(),
        "save-to-spotify": shutil.which("save-to-spotify") or "not found",
        "templates": {"count": len(template_names), "names": template_names},
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="readio", description="Stream text to PyKokoro TTS")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
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
    render.add_argument("--force", action="store_true", help="replace an existing output")
    _add_runtime_options(render, playback=False)
    render.set_defaults(func=_cmd_render)

    spotify = sub.add_parser("spotify", help="render and publish speech through save-to-spotify")
    _add_input_options(spotify)
    _add_audio_output_options(spotify)
    spotify.add_argument("--title", required=True, help="episode title")
    show = spotify.add_mutually_exclusive_group()
    show.add_argument("--show-id", help="Spotify show ID or URI")
    show.add_argument("--new-show", help="create or select a new show")
    spotify.add_argument("--summary")
    spotify.add_argument("--image", type=Path)
    spotify.add_argument("--language")
    spotify.add_argument("--wait", action="store_true", help="wait for Spotify readiness")
    spotify.add_argument("--wait-timeout", help="readiness timeout, for example 2m")
    spotify.add_argument(
        "--output",
        type=Path,
        help="keep the generated audio at this path (.wav, .mp3, .m4a, or .ogg)",
    )
    spotify.add_argument("--force", action="store_true", help="replace an existing output")
    spotify.add_argument("--json", action="store_true", help="emit one JSON result object")
    spotify.add_argument(
        "--chapters-from-markers",
        action="store_true",
        help="publish SSMD markers as Spotify chapters",
    )
    _add_runtime_options(spotify, playback=False)
    spotify.set_defaults(func=_cmd_spotify)

    voices = sub.add_parser("voices", help="discover and manage SSMD voices")
    voices_sub = voices.add_subparsers(dest="voices_command", required=True)
    voices_list = voices_sub.add_parser("list", help="list configured concrete voices")
    voices_list.add_argument("--provider")
    voices_list.add_argument("--json", action="store_true")
    voices_list.set_defaults(func=_cmd_voices)
    voices_roles = voices_sub.add_parser("roles", help="list configured logical roles")
    voices_roles.add_argument("--provider")
    voices_roles.add_argument("--json", action="store_true")
    voices_roles.set_defaults(func=_cmd_voices)
    voices_bind = voices_sub.add_parser("bind", help="persist a logical role binding")
    voices_bind.add_argument("role")
    voices_bind.add_argument("voice_id")
    voices_bind.add_argument("--provider")
    voices_bind.add_argument("--json", action="store_true")
    voices_bind.set_defaults(func=_cmd_voices)
    voices_unbind = voices_sub.add_parser("unbind", help="remove a logical role binding")
    voices_unbind.add_argument("role")
    voices_unbind.add_argument("--provider")
    voices_unbind.add_argument("--json", action="store_true")
    voices_unbind.set_defaults(func=_cmd_voices)

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
    return parser


def _error_payload(exc: ReadioError) -> dict[str, object]:
    payload: dict[str, object] = {"ok": False, "code": exc.code, "error": str(exc)}
    if exc.source_path is not None:
        payload["source"] = str(exc.source_path)
    for name in ("provider", "reference"):
        value = getattr(exc, name, None)
        if value is not None:
            payload[name] = value
    references = getattr(exc, "references", None)
    if references:
        payload["references"] = [
            {
                "name": item.reference,
                "count": item.count,
                "lines": list(item.lines),
            }
            for item in references
        ]
    for name in ("available_voices", "header_template"):
        value = getattr(exc, name, None)
        if value:
            payload[name] = _json_value(value)
    return payload


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        code = args.func(args)
    except ReadioError as exc:
        if getattr(args, "json", False):
            print(json.dumps(_error_payload(exc), ensure_ascii=False))
            raise SystemExit(2)
        source = f" (source: {exc.source_path})" if exc.source_path else ""
        parser.exit(2, f"readio: {exc}{source}\n")
    except (
        ValueError,
        SelectionError,
        SpotifyError,
        KeyError,
        OSError,
        ImportError,
    ) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            raise SystemExit(2)
        parser.exit(2, f"readio: {exc}\n")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
