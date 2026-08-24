from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .audio import RenderSummary
from .config import (
    ReaderConfig,
    config_path,
    load_config,
    save_config,
    set_config_value,
    with_overrides,
)
from .reader import SelectionError, render_live, render_text, speak_live, speak_text
from .spotify import (
    SpotifyError,
    build_timeline,
    set_timeline,
    upload_episode,
    wait_for_episode,
)
from .wave import WaveSink, atomic_wav_path


def _add_input_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("text", nargs="*", help="literal text; omit to read stdin")
    parser.add_argument("--file", type=Path, help="read UTF-8 text from a file")
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


def _validate_live(args: argparse.Namespace) -> None:
    if args.file is not None or args.text:
        raise ValueError("--live reads stdin only; do not combine it with text or --file")
    if args.select != "all":
        raise ValueError("--select is not available with --live")
    if sys.stdin.isatty():
        raise ValueError("--live requires piped stdin")


def _cmd_speak(args: argparse.Namespace) -> int:
    cfg = _resolved_config(args)
    if args.live:
        _validate_live(args)
        speak_live(sys.stdin, cfg, unit=args.unit)
    else:
        speak_text(_read_input(args), cfg, selector=args.select, unit=args.unit)
    return 0


def _render_audio(args: argparse.Namespace, path: Path) -> RenderSummary:
    cfg = _resolved_config(args)
    with WaveSink(path) as sink:
        if args.live:
            return render_live(sys.stdin, cfg, sink, unit=args.unit)
        return render_text(_read_input(args), cfg, sink, selector=args.select, unit=args.unit)


def _cmd_render(args: argparse.Namespace) -> int:
    if args.live:
        _validate_live(args)
    with atomic_wav_path(args.output, force=args.force) as temporary:
        _render_audio(args, temporary)
    print(args.output)
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
        audio_path = args.output

    try:
        if args.output is None:
            summary = _render_audio(args, audio_path)
        else:
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
    try:
        import soundfile as sf

        checks["soundfile"] = getattr(sf, "__libsndfile_version__", "installed")
    except (ImportError, OSError) as exc:  # pragma: no cover - environment dependent
        checks["soundfile"] = f"ERROR: {exc}"
    checks["save-to-spotify"] = shutil.which("save-to-spotify") or "not found"
    print(json.dumps(checks, indent=2))
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
    render.add_argument("-o", "--output", type=Path, required=True, help="output WAV path")
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
    except (ValueError, SelectionError, SpotifyError, KeyError, OSError, ImportError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            raise SystemExit(2)
        parser.exit(2, f"readio: {exc}\n")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
