from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .config import (
    ReaderConfig,
    config_path,
    load_config,
    save_config,
    set_config_value,
    with_overrides,
)
from .reader import SelectionError, speak_live, speak_text


def _add_runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--voice", help="PyKokoro voice, e.g. af_sarah")
    parser.add_argument("--lang", help="language code, e.g. en-us, de, fr")
    parser.add_argument("--speed", type=float, help="speech speed multiplier")
    parser.add_argument("--pause-mode", choices=("tts", "manual", "auto"))
    parser.add_argument("--unit", choices=("sentence", "paragraph"))
    parser.add_argument("--queue-size", type=int, help="audio queue depth")
    parser.add_argument("--device", help="sounddevice output device name or id")


def _resolved_config(args: argparse.Namespace) -> ReaderConfig:
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


def _read_input(args: argparse.Namespace) -> str:
    if args.file is not None:
        return args.file.read_text(encoding="utf-8")
    if args.text:
        return " ".join(args.text)
    if sys.stdin.isatty():
        raise ValueError("provide text, --file PATH, or pipe text on stdin")
    return sys.stdin.read()


def _cmd_speak(args: argparse.Namespace) -> int:
    cfg = _resolved_config(args)
    if args.live:
        if args.file is not None or args.text:
            raise ValueError("--live reads stdin only; do not combine it with text or --file")
        if args.select != "all":
            raise ValueError("--select is not available with --live")
        if sys.stdin.isatty():
            raise ValueError("--live requires piped stdin")
        speak_live(sys.stdin, cfg, unit=args.unit)
        return 0
    speak_text(_read_input(args), cfg, selector=args.select, unit=args.unit)
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    path = config_path()
    if args.config_command == "path":
        print(path)
        return 0
    if args.config_command == "show":
        print(json.dumps(asdict(load_config(path)), indent=2, ensure_ascii=False))
        return 0
    if args.config_command == "init":
        if path.exists() and not args.force:
            raise ValueError(f"config already exists: {path}; use --force to replace it")
        save_config(ReaderConfig(), path)
        print(path)
        return 0
    if args.config_command == "set":
        cfg = set_config_value(load_config(path), args.key, args.value)
        save_config(cfg, path)
        print(path)
        return 0
    raise AssertionError("unreachable")


def _cmd_doctor(_: argparse.Namespace) -> int:
    checks: dict[str, str] = {"readio": __version__, "config": str(config_path())}
    try:
        import pykokoro

        checks["pykokoro"] = getattr(pykokoro, "__version__", "installed")
    except (ImportError, OSError) as exc:  # pragma: no cover - environment dependent
        checks["pykokoro"] = f"ERROR: {exc}"
    try:
        import sounddevice as sd

        checks["sounddevice"] = getattr(sd, "__version__", "installed")
    except (ImportError, OSError) as exc:  # pragma: no cover - environment dependent
        checks["sounddevice"] = f"ERROR: {exc}"
    print(json.dumps(checks, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="readio", description="Stream text to PyKokoro TTS")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    speak = sub.add_parser("speak", help="read text aloud")
    speak.add_argument("text", nargs="*", help="literal text; omit to read stdin")
    speak.add_argument("--file", type=Path, help="read UTF-8 text from a file")
    speak.add_argument(
        "--select",
        default="all",
        metavar="SELECTOR",
        help="all (default), last-paragraph, or paragraph:N",
    )
    speak.add_argument(
        "--live",
        action="store_true",
        help="consume stdin incrementally and start each paragraph when a blank line closes it",
    )
    _add_runtime_options(speak)
    speak.set_defaults(func=_cmd_speak)

    cfg = sub.add_parser("config", help="manage persistent configuration")
    cfg_sub = cfg.add_subparsers(dest="config_command", required=True)
    cfg_sub.add_parser("path", help="print config path")
    cfg_sub.add_parser("show", help="show effective persisted config as JSON")
    init = cfg_sub.add_parser("init", help="write the default config")
    init.add_argument("--force", action="store_true")
    set_cmd = cfg_sub.add_parser("set", help="set one config key")
    set_cmd.add_argument("key")
    set_cmd.add_argument("value")
    cfg.set_defaults(func=_cmd_config)

    doctor = sub.add_parser("doctor", help="check runtime dependencies")
    doctor.set_defaults(func=_cmd_doctor)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        code = args.func(args)
    except (ValueError, SelectionError, KeyError, OSError, ImportError) as exc:
        parser.exit(2, f"readio: {exc}\n")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
