from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from . import cli_adapter
from .api import (
    G2P_FALLBACKS,
    LANGUAGE_DETECTION_MODES,
    LEXICON_DATA_POLICIES,
    SHORT_SENTENCE_POLICIES,
    SPACY_POLICIES,
    SUPPORTED_AUDIO_FORMATS,
    VOICE_LEVEL_MODES,
    Document,
    EventHandler,
    OutputRequest,
    PlanRequest,
    Readio,
    SynthesisRequest,
)
from .api.integrations.spotify import (
    SpotifyLivePublishRequest,
    SpotifyPublishRequest,
    SpotifyService,
    SpotifyUploadRequest,
)
from .progress import TerminalProgress

logger = logging.getLogger(__name__)


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit one JSON result object")


def _add_wait(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--wait",
        nargs="?",
        const="",
        default=None,
        metavar="DURATION",
        help="wait for Spotify readiness, optionally for a duration such as 2m",
    )
    parser.add_argument(
        "--wait-timeout",
        dest="wait_timeout_compat",
        help="deprecated alias for --wait DURATION",
    )


def _add_spotify_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--title", required=True, help="episode title")
    show = parser.add_mutually_exclusive_group()
    show.add_argument("--show-id", help="Spotify show ID or URI")
    show.add_argument("--new-show", help="create a new Spotify show with this title")
    parser.add_argument("--summary")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--language")
    parser.add_argument("--api-timeout", help="timeout for one save-to-spotify API request")
    _add_wait(parser)
    timeline = parser.add_mutually_exclusive_group()
    timeline.add_argument("--timeline", type=Path, help="pass a caller-owned timeline JSON file")
    timeline.add_argument(
        "--chapters-from-markers",
        action="store_true",
        help="publish SSMD markers as Spotify chapters",
    )
    _add_json(parser)


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
        choices=SUPPORTED_AUDIO_FORMATS,
        help="audio output format; inferred from --output when possible; default: wav",
    )


def _add_synthesis_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--engine", help="synthesis backend; default from configuration")
    parser.add_argument("--offline", action="store_true", help="do not fetch engine assets")
    parser.add_argument("--refresh", action="store_true", help="refresh engine discovery metadata")
    parser.add_argument("--voice", help="stable selector or canonical backend voice ID")
    parser.add_argument("--speaker", help="named or numeric speaker for multi-speaker engines")
    parser.add_argument("--lang", help="language code, e.g. en-us, de, fr")
    parser.add_argument("--model", help="runtime model ID")
    parser.add_argument("--model-source", choices=("github", "huggingface"))
    parser.add_argument("--quality", help="model quality/quantization")
    lexicon_group = parser.add_mutually_exclusive_group()
    lexicon_group.add_argument("--lexicon", dest="lexicons", action="append", metavar="NAME")
    lexicon_group.add_argument("--no-lexicons", action="store_true")
    lexicon_group.add_argument("--auto-lexicons", action="store_true")
    parser.add_argument("--g2p-fallback", choices=G2P_FALLBACKS)
    parser.add_argument("--lexicon-data-policy", choices=LEXICON_DATA_POLICIES)
    parser.add_argument("--spacy", choices=SPACY_POLICIES)
    parser.add_argument(
        "--short-sentence",
        choices=SHORT_SENTENCE_POLICIES,
    )
    parser.add_argument("--language-detection", choices=LANGUAGE_DETECTION_MODES)
    parser.add_argument(
        "--detect-language", dest="detect_languages", action="append", metavar="LANG"
    )
    parser.add_argument("--allow-experimental", action="store_true")
    parser.add_argument("--speed", type=float, help="speech speed multiplier")
    parser.add_argument("--voice-level", choices=VOICE_LEVEL_MODES)
    parser.add_argument("--pause-mode", choices=("tts", "manual", "auto"))
    parser.add_argument("--unit", choices=("sentence", "paragraph"))
    parser.add_argument("--voice-bind", action="append", default=[], metavar="ROLE=VOICE_ID")
    parser.add_argument("--resolve-voices", action="store_true")


def _add_progress_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "show progress on stderr; enabled automatically on an interactive terminal, "
            "use --no-progress to disable"
        ),
    )


def _wait_arguments(args: argparse.Namespace) -> tuple[bool, str | None]:
    duration = args.wait if args.wait is not None else getattr(args, "wait_timeout_compat", None)
    return duration is not None, (duration or None)


def _normalize_positional_input(args: argparse.Namespace) -> None:
    cli_adapter.normalize_positional_input(args)


def _document(args: argparse.Namespace) -> Document:
    return cli_adapter.read_document(args)


def _synthesis_request(args: argparse.Namespace) -> SynthesisRequest:
    return cli_adapter.synthesis_request_from_args(args)


def _voice_bindings(values: list[str]) -> dict[str, str]:
    return cli_adapter.parse_voice_bindings(values)


def _resolve_interactive_bindings(
    app: Readio,
    document: Document,
    synthesis: SynthesisRequest,
    bindings: dict[str, str],
    args: argparse.Namespace,
) -> dict[str, str]:
    if document.format != "ssmd" or not args.resolve_voices:
        return bindings
    analysis = app.ssmd.check(document, synthesis=synthesis).analysis
    if not set(analysis.unresolved_references).difference(bindings):
        return bindings
    if args.json or not sys.stdin.isatty():
        raise ValueError(
            "--resolve-voices requires an interactive terminal; "
            "provide --voice-bind ROLE=VOICE_ID instead"
        )
    bindings.update(
        cli_adapter.prompt_missing_voices(analysis, app.config, synthesis, bindings=bindings)
    )
    return bindings


def _plan_request(app: Readio, args: argparse.Namespace) -> PlanRequest:
    _normalize_positional_input(args)
    document = _document(args)
    synthesis = _synthesis_request(args)
    bindings = _resolve_interactive_bindings(
        app,
        document,
        synthesis,
        _voice_bindings(args.voice_bind),
        args,
    )
    input_format = args.input_format
    if input_format == "auto" and args.file is None:
        input_format = "text"
    output = OutputRequest(
        mode="file",
        requested_format=args.format,
        requested_path=args.output,
        force=args.force,
    )
    return cli_adapter.build_plan_request(
        args,
        document=document,
        synthesis=synthesis,
        output=output,
        voice_bindings=bindings,
        operation="render",
        requested_format=input_format,
    )


def _progress(args: argparse.Namespace) -> TerminalProgress:
    stream = sys.stderr
    explicit = args.progress
    enabled = explicit if explicit is not None else not args.json and bool(stream.isatty())
    return TerminalProgress(
        stream=stream,
        enabled=enabled,
        tty=bool(stream.isatty()) and getattr(args, "verbose", 0) == 0,
    )


def _progress_handler(progress: TerminalProgress) -> EventHandler | None:
    return cli_adapter.public_event_handler(progress)


def _result_payload(result: Any) -> dict[str, object]:
    return {
        "ok": True,
        "episode_uri": result.episode_uri,
        "upload_status": result.upload_status,
        "readiness": result.readiness.readiness if result.readiness is not None else None,
        "audio_path": str(result.audio_path) if result.audio_path is not None else None,
        "audio_format": result.audio_format,
        "timeline_published": result.timeline_published,
    }


def _print_result(result: dict[str, object], *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(result, ensure_ascii=False))
        return
    print(f"Published: {result['episode_uri']}")
    if result["readiness"] is not None:
        print(f"Readiness: {result['readiness']}")
    if result["audio_path"] is not None:
        print(f"Audio: {result['audio_path']}")
    if result["timeline_published"]:
        print("Timeline: published")


def _live_publish(
    args: argparse.Namespace, app: Readio, progress: TerminalProgress
) -> dict[str, object]:
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

    wait, wait_timeout = _wait_arguments(args)
    progress.render_started()
    progress.phase("Preparing", "live input")
    progress.phase("Loading TTS")
    result = SpotifyService(app).publish_live(
        SpotifyLivePublishRequest(
            lines=sys.stdin,
            output=OutputRequest(
                requested_format=args.format,
                requested_path=args.output,
                force=args.force,
            ),
            synthesis=_synthesis_request(args),
            title=args.title,
            show_id=args.show_id,
            new_show=args.new_show,
            summary=args.summary,
            image=args.image,
            language=args.language,
            timeline=args.timeline,
            chapters_from_markers=args.chapters_from_markers,
            wait=wait,
            wait_timeout=wait_timeout,
            api_timeout=args.api_timeout,
            unit=args.unit,
        ),
        on_event=_progress_handler(progress),
    )
    if result.render_summary is not None:
        progress.complete(result.render_summary)
    return _result_payload(result)


def cmd_spotify_publish(args: argparse.Namespace) -> int:
    logger.info("spotify.publish.start")
    app = Readio()
    progress = _progress(args)
    progress.__enter__()
    try:
        if args.live:
            result = _live_publish(args, app, progress)
        else:
            request = SpotifyPublishRequest(
                render=_plan_request(app, args),
                title=args.title,
                show_id=args.show_id,
                new_show=args.new_show,
                summary=args.summary,
                image=args.image,
                language=args.language,
                timeline=args.timeline,
                chapters_from_markers=args.chapters_from_markers,
                wait=_wait_arguments(args)[0],
                wait_timeout=_wait_arguments(args)[1],
                api_timeout=args.api_timeout,
            )
            result_value = SpotifyService(app).publish(
                request, on_event=_progress_handler(progress)
            )
            if result_value.render_summary is not None:
                progress.complete(result_value.render_summary)
            result = _result_payload(result_value)
        if args.output is not None:
            result["audio_path"] = str(args.output)
        _print_result(result, json_mode=args.json)
        return 0
    finally:
        progress.close()


def cmd_spotify_upload(args: argparse.Namespace) -> int:
    logger.info("spotify.upload.start audio=%s", args.audio)
    wait, wait_timeout = _wait_arguments(args)
    result = SpotifyService(Readio()).upload(
        SpotifyUploadRequest(
            audio_path=args.audio,
            title=args.title,
            show_id=args.show_id,
            new_show=args.new_show,
            summary=args.summary,
            image=args.image,
            language=args.language,
            timeline=args.timeline,
            wait=wait,
            wait_timeout=wait_timeout,
            api_timeout=args.api_timeout,
        )
    )
    payload = _result_payload(result)
    payload["audio_path"] = None
    _print_result(payload, json_mode=args.json)
    return 0


def cmd_spotify_shows(args: argparse.Namespace) -> int:
    logger.info("spotify.shows.start")
    shows = SpotifyService(Readio()).shows(api_timeout=args.api_timeout)
    result = {
        "ok": True,
        "shows": [
            {"show_uri": item.show_uri, "title": item.title, "language": item.language}
            for item in shows
        ],
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        for item in result["shows"]:
            print(f"{item['show_uri']}  {item['title']}")
    return 0


def cmd_spotify_status(args: argparse.Namespace) -> int:
    wait, wait_timeout = _wait_arguments(args)
    logger.info("spotify.status.start episode=%s", args.episode)
    status = SpotifyService(Readio()).status(
        args.episode,
        wait=wait,
        wait_timeout=wait_timeout,
        api_timeout=args.api_timeout,
    )
    result = {"ok": True, "episode_uri": status.episode_uri, "readiness": status.readiness}
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"{status.episode_uri}: {status.readiness}")
    return 0


def cmd_spotify_doctor(args: argparse.Namespace) -> int:
    logger.info("spotify.doctor.start")
    payload = SpotifyService(Readio()).doctor(api_timeout=args.api_timeout)
    result = {"ok": True, **payload.details}
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("Spotify integration: OK")
        for key, value in payload.details.items():
            if key != "ok":
                print(f"{key}: {value}")
    return 0


def add_spotify_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    spotify = subparsers.add_parser(
        "spotify", help="render, upload, and inspect media through save-to-spotify"
    )
    spotify_sub = spotify.add_subparsers(dest="spotify_command", required=True)

    publish = spotify_sub.add_parser("publish", help="render Readio input and publish an episode")
    _add_input_options(publish)
    _add_audio_output_options(publish)
    _add_spotify_options(publish)
    publish.add_argument(
        "--output",
        type=Path,
        help="keep the generated audio at this path (.wav, .mp3, .m4a, or .ogg)",
    )
    publish.add_argument("--force", action="store_true", help="replace an existing output")
    _add_synthesis_options(publish)
    _add_progress_option(publish)
    publish.set_defaults(func=cmd_spotify_publish)

    upload = spotify_sub.add_parser("upload", help="upload an existing audio file")
    upload.add_argument("audio", type=Path, help="existing .wav, .mp3, .m4a, or .ogg file")
    _add_spotify_options(upload)
    upload.set_defaults(func=cmd_spotify_upload)

    shows = spotify_sub.add_parser("shows", help="list available Spotify shows")
    shows.add_argument("--api-timeout", help="timeout for one save-to-spotify API request")
    _add_json(shows)
    shows.set_defaults(func=cmd_spotify_shows)

    status = spotify_sub.add_parser("status", help="inspect or wait for episode readiness")
    status.add_argument("episode", help="Spotify episode ID or URI")
    status.add_argument("--api-timeout", help="timeout for one save-to-spotify API request")
    _add_wait(status)
    _add_json(status)
    status.set_defaults(func=cmd_spotify_status)

    integration_doctor = spotify_sub.add_parser("doctor", help="check the Spotify integration")
    integration_doctor.add_argument(
        "--api-timeout", help="timeout for one save-to-spotify API request"
    )
    _add_json(integration_doctor)
    integration_doctor.set_defaults(func=cmd_spotify_doctor)
