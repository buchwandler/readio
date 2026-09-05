from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from . import cli as _cli
from .formats import audio_format_from_suffix, format_suffix, resolve_audio_format
from .spotify import (
    SpotifyError,
    SpotifyReadinessResult,
    SpotifyUploadResult,
    build_timeline,
    doctor,
    episode_status,
    list_shows,
    set_timeline,
    upload_episode,
)


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


def _wait_arguments(args: argparse.Namespace) -> tuple[bool, str | None]:
    duration = args.wait if args.wait is not None else getattr(args, "wait_timeout_compat", None)
    return duration is not None, (duration or None)


def _read_timeline(path: Path) -> Path:
    if not path.exists():
        raise ValueError(f"timeline file does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"timeline file is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise TypeError("timeline JSON must contain an object")
    return path


def _result(
    uploaded: SpotifyUploadResult,
    readiness: SpotifyReadinessResult | None,
    *,
    audio_path: Path | None,
    audio_format: str,
    timeline_published: bool,
) -> dict[str, object]:
    return {
        "ok": True,
        "episode_uri": uploaded.episode_uri,
        "upload_status": uploaded.status,
        "readiness": readiness.readiness if readiness is not None else None,
        "audio_path": str(audio_path) if audio_path is not None else None,
        "audio_format": audio_format,
        "timeline_published": timeline_published,
    }


def _publish_uploaded(
    audio_path: Path,
    args: argparse.Namespace,
    *,
    audio_format: str,
    timeline_path: Path | None,
    marker_timeline: dict[str, Any] | None,
) -> dict[str, object]:
    uploaded = upload_episode(
        audio_path,
        title=args.title,
        show_id=args.show_id,
        new_show=args.new_show,
        summary=args.summary,
        image=args.image,
        language=args.language,
        api_timeout=args.api_timeout,
    )
    wait, wait_duration = _wait_arguments(args)
    readiness: SpotifyReadinessResult | None = None
    if wait or timeline_path is not None or marker_timeline is not None:
        readiness = episode_status(
            uploaded.episode_uri,
            wait=True,
            wait_timeout=wait_duration,
            api_timeout=args.api_timeout,
        )
    timeline_published = False
    if timeline_path is not None or marker_timeline is not None:
        if readiness is None or readiness.readiness != "READY":
            raise SpotifyError("episode is not READY; cannot set timeline")
        temporary_timeline: Path | None = None
        try:
            if marker_timeline is not None:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", prefix="readio-timeline-", delete=False
                ) as timeline_file:
                    json.dump(marker_timeline, timeline_file)
                    temporary_timeline = Path(timeline_file.name)
                timeline_path = temporary_timeline
            assert timeline_path is not None
            set_timeline(uploaded.episode_uri, timeline_path, api_timeout=args.api_timeout)
            timeline_published = True
        finally:
            if temporary_timeline is not None:
                temporary_timeline.unlink(missing_ok=True)
    return _result(
        uploaded,
        readiness,
        audio_path=getattr(args, "output", None),
        audio_format=audio_format,
        timeline_published=timeline_published,
    )


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


def cmd_spotify_publish(args: argparse.Namespace) -> int:
    _cli._normalize_positional_input(args)
    if args.live:
        _cli._validate_live(args)
    cfg = _cli._resolved_config(args)
    args._resolved_synthesis = _cli.resolve_synthesis(cfg, args)
    audio_format = resolve_audio_format(requested=args.format, output=args.output)
    _cli.ensure_audio_format_available(audio_format)
    progress = _cli._build_progress(args)
    progress.__enter__()
    temporary: Path | None = None
    try:
        progress.phase("Preparing", _cli._progress_source_label(args))
        prepared = None if args.live else _cli._prepared_input(args, cfg)
        args._prepared_cfg = cfg
        if prepared:
            args._prepared_document, args._prepared_bindings = prepared
        if args.timeline is not None:
            _read_timeline(args.timeline)
        if args.output is None:
            fd, name = tempfile.mkstemp(prefix="readio-", suffix=format_suffix(audio_format))
            os.close(fd)
            temporary = Path(name)
            audio_path = temporary
        else:
            audio_path = _cli.normalize_audio_output_path(args.output.expanduser(), audio_format)
        progress.phase("Loading TTS")
        if args.output is None:
            summary = _cli._render_audio(
                args,
                audio_path,
                audio_format=audio_format,
                **_cli._progress_kwargs(progress),
            )
        else:
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            with _cli.atomic_audio_path(audio_path, force=args.force) as target:
                summary = _cli._render_audio(
                    args,
                    target,
                    audio_format=audio_format,
                    **_cli._progress_kwargs(progress),
                )
        progress.complete(summary)
        marker_timeline = (
            build_timeline(summary.markers, summary.sample_rate)
            if args.chapters_from_markers
            else None
        )
        progress.phase("Uploading")
        if args.timeline is not None or marker_timeline is not None:
            progress.phase("Waiting for Spotify readiness")
        result = _publish_uploaded(
            audio_path,
            args,
            audio_format=audio_format,
            timeline_path=args.timeline,
            marker_timeline=marker_timeline,
        )
        _print_result(result, json_mode=args.json)
        return 0
    finally:
        progress.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def cmd_spotify_upload(args: argparse.Namespace) -> int:
    if not args.audio.exists():
        raise ValueError(f"audio file does not exist: {args.audio}")
    audio_format = audio_format_from_suffix(args.audio)
    if audio_format is None:
        raise ValueError("Spotify upload supports .wav, .mp3, .m4a, and .ogg files")
    timeline_path = _read_timeline(args.timeline) if args.timeline is not None else None
    result = _publish_uploaded(
        args.audio,
        args,
        audio_format=audio_format,
        timeline_path=timeline_path,
        marker_timeline=None,
    )
    _print_result(result, json_mode=args.json)
    return 0


def cmd_spotify_shows(args: argparse.Namespace) -> int:
    shows = list_shows(api_timeout=args.api_timeout)
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
            assert isinstance(item, dict)
            print(f"{item['show_uri']}  {item['title']}")
    return 0


def cmd_spotify_status(args: argparse.Namespace) -> int:
    wait, duration = _wait_arguments(args)
    status = episode_status(
        args.episode,
        wait=wait,
        wait_timeout=duration,
        api_timeout=args.api_timeout,
    )
    result = {"ok": True, "episode_uri": status.episode_uri, "readiness": status.readiness}
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"{status.episode_uri}: {status.readiness}")
    return 0


def cmd_spotify_doctor(args: argparse.Namespace) -> int:
    payload = doctor(api_timeout=args.api_timeout)
    result = {"ok": True, **payload}
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("Spotify integration: OK")
        for key, value in payload.items():
            if key != "ok":
                print(f"{key}: {value}")
    return 0


def add_spotify_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    spotify = subparsers.add_parser(
        "spotify", help="render, upload, and inspect media through save-to-spotify"
    )
    spotify_sub = spotify.add_subparsers(dest="spotify_command", required=True)

    publish = spotify_sub.add_parser("publish", help="render Readio input and publish an episode")
    _cli._add_input_options(publish)
    _cli._add_audio_output_options(publish)
    _add_spotify_options(publish)
    publish.add_argument(
        "--output",
        type=Path,
        help="keep the generated audio at this path (.wav, .mp3, .m4a, or .ogg)",
    )
    publish.add_argument("--force", action="store_true", help="replace an existing output")
    _cli._add_runtime_options(publish, playback=False)
    _cli._add_progress_option(publish)
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
