from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .audio import RenderSummary
from .config import (
    ReadioConfig,
    config_path,
    default_config,
    load_config,
    save_config,
    set_config_value,
    validate_config,
    with_overrides,
)
from .document import InputDocument, document_from_file, document_from_stdin, document_from_text
from .errors import ReadioError
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
from .ssmd import preflight_ssmd
from .ssmd_authoring import roundtrip_check
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
from .wave import WaveSink, atomic_wav_path


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


def _add_synthesis_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--voice", help="PyKokoro voice, e.g. af_sarah")
    parser.add_argument("--lang", help="language code, e.g. en-us, de, fr")
    parser.add_argument("--speed", type=float, help="speech speed multiplier")
    parser.add_argument("--pause-mode", choices=("tts", "manual", "auto"))
    parser.add_argument("--unit", choices=("sentence", "paragraph"))


def _add_runtime_options(parser: argparse.ArgumentParser, *, playback: bool = True) -> None:
    _add_synthesis_options(parser)
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


def _cmd_speak(args: argparse.Namespace) -> int:
    cfg = _resolved_config(args)
    if args.live:
        _validate_live(args)
        speak_live(sys.stdin, cfg.reader, unit=args.unit)
    else:
        speak_text(_read_input(args, cfg), cfg, selector=args.select, unit=args.unit)
    return 0


def _render_audio(args: argparse.Namespace, path: Path) -> RenderSummary:
    cfg = _resolved_config(args)
    with WaveSink(path) as sink:
        if args.live:
            return render_live(sys.stdin, cfg, sink, unit=args.unit)
        return render_text(_read_input(args, cfg), cfg, sink, selector=args.select, unit=args.unit)


def _cmd_render(args: argparse.Namespace) -> int:
    if args.live:
        _validate_live(args)
    cfg = _resolved_config(args)
    output = resolve_render_output(cfg, explicit=args.output, input_path=args.file)
    output.parent.mkdir(parents=True, exist_ok=True)
    with atomic_wav_path(output, force=args.force) as temporary:
        _render_audio(args, temporary)
    print(output)
    return 0


def _cmd_spotify(args: argparse.Namespace) -> int:
    if args.wait_timeout is not None and not (args.wait or args.chapters_from_markers):
        raise ValueError("--wait-timeout requires --wait")
    if args.live:
        _validate_live(args)

    temporary: Path | None = None
    if args.output is None:
        fd, name = tempfile.mkstemp(prefix="readio-", suffix=".wav")
        os.close(fd)
        temporary = Path(name)
        audio_path = temporary
    else:
        audio_path = args.output.expanduser()

    try:
        if args.output is None:
            summary = _render_audio(args, audio_path)
        else:
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            with atomic_wav_path(audio_path, force=args.force) as target:
                summary = _render_audio(args, target)
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
            "audio_path": str(args.output) if args.output is not None else None,
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
    document = document_from_file(args.file)
    consumer = preflight_ssmd(document.text, cfg, source_path=document.source_path)
    result: dict[str, object] = {
        "ok": consumer.ok,
        "source": str(document.source_path),
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

    render = sub.add_parser("render", help="render text to a WAV file")
    _add_input_options(render)
    render.add_argument("-o", "--output", type=Path, help="output WAV path")
    render.add_argument("--force", action="store_true", help="replace an existing output")
    _add_runtime_options(render, playback=False)
    render.set_defaults(func=_cmd_render)

    spotify = sub.add_parser("spotify", help="render and publish speech through save-to-spotify")
    _add_input_options(spotify)
    spotify.add_argument("--title", required=True, help="episode title")
    show = spotify.add_mutually_exclusive_group()
    show.add_argument("--show-id", help="Spotify show ID or URI")
    show.add_argument("--new-show", help="create or select a new show")
    spotify.add_argument("--summary")
    spotify.add_argument("--image", type=Path)
    spotify.add_argument("--language")
    spotify.add_argument("--wait", action="store_true", help="wait for Spotify readiness")
    spotify.add_argument("--wait-timeout", help="readiness timeout, for example 2m")
    spotify.add_argument("--output", type=Path, help="keep the generated WAV at this path")
    spotify.add_argument("--force", action="store_true", help="replace an existing output")
    spotify.add_argument("--json", action="store_true", help="emit one JSON result object")
    spotify.add_argument(
        "--chapters-from-markers",
        action="store_true",
        help="publish SSMD markers as Spotify chapters",
    )
    _add_runtime_options(spotify, playback=False)
    spotify.set_defaults(func=_cmd_spotify)

    ssmd = sub.add_parser("ssmd", help="inspect SSMD documents")
    ssmd_sub = ssmd.add_subparsers(dest="ssmd_command", required=True)
    check = ssmd_sub.add_parser("check", help="check an SSMD document for Readio consumption")
    check.add_argument("file", type=Path)
    check.add_argument("--roundtrip", action="store_true")
    check.add_argument("--json", action="store_true")
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
