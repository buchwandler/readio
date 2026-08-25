from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SpotifyError(RuntimeError):
    """Base error for the external save-to-spotify integration."""


class SpotifyUnavailableError(SpotifyError):
    """The save-to-spotify executable is unavailable."""


class SpotifyProtocolError(SpotifyError):
    """The external CLI returned an unusable response."""


class SpotifyCommandError(SpotifyError):
    """The external CLI reported a command failure."""


@dataclass(frozen=True, slots=True)
class SpotifyVersion:
    version: str
    commit: str | None = None


@dataclass(frozen=True, slots=True)
class SpotifyUploadResult:
    episode_uri: str
    status: str | None


@dataclass(frozen=True, slots=True)
class SpotifyReadinessResult:
    episode_uri: str
    readiness: str


@dataclass(frozen=True, slots=True)
class SpotifyShow:
    show_uri: str
    title: str
    language: str | None = None


def executable() -> str:
    """Resolve the upstream executable without inspecting its credentials."""

    path = shutil.which("save-to-spotify")
    if path is None:
        raise SpotifyUnavailableError("save-to-spotify executable not found on PATH")
    return path


# Kept as a private compatibility alias for existing callers/tests.
def _executable() -> str:
    return executable()


def run_save_to_spotify(
    args: Sequence[str],
    *,
    api_timeout: str | None = None,
) -> dict[str, Any]:
    """Run one upstream command and return its object JSON response.

    The upstream CLI owns authentication and credential storage. Readio only
    passes command arguments, captures the response, and translates protocol
    failures into stable local error types.
    """

    command = [executable(), "--json"]
    if api_timeout is not None:
        command.extend(("--timeout", api_timeout))
    command.extend(args)
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        if completed.returncode == 0:
            raise SpotifyProtocolError("save-to-spotify returned invalid JSON") from exc
        detail = completed.stderr.strip() or "save-to-spotify failed"
        raise SpotifyCommandError(f"{detail} (exit code {completed.returncode})") from exc
    if not isinstance(payload, dict):
        raise SpotifyProtocolError("save-to-spotify returned a JSON object")
    if completed.returncode != 0:
        detail = payload.get("error") or completed.stderr.strip() or "save-to-spotify failed"
        raise SpotifyCommandError(f"{detail} (exit code {completed.returncode})")
    if payload.get("ok") is False:
        detail = payload.get("error") or "save-to-spotify reported an error"
        raise SpotifyCommandError(str(detail))
    return payload


# Compatibility for code that used the old private runner. New code should use
# run_save_to_spotify so all upstream calls share the same contract.
def _run_json(command: list[str], *, invalid_message: str) -> dict[str, Any]:
    del invalid_message
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        if completed.returncode == 0:
            raise SpotifyProtocolError("save-to-spotify returned invalid JSON") from exc
        detail = completed.stderr.strip() or "save-to-spotify failed"
        raise SpotifyCommandError(f"{detail} (exit code {completed.returncode})") from exc
    if not isinstance(payload, dict):
        raise SpotifyProtocolError("save-to-spotify returned a JSON object")
    if completed.returncode != 0 or payload.get("ok") is False:
        detail = payload.get("error") or completed.stderr.strip() or "save-to-spotify failed"
        raise SpotifyCommandError(f"{detail} (exit code {completed.returncode})")
    return payload


def version(*, api_timeout: str | None = None) -> SpotifyVersion:
    payload = run_save_to_spotify(("version",), api_timeout=api_timeout)
    value = payload.get("version")
    if not isinstance(value, str) or not value:
        raise SpotifyProtocolError("save-to-spotify response is missing version")
    commit = payload.get("commit")
    return SpotifyVersion(value, commit if isinstance(commit, str) else None)


def doctor(*, api_timeout: str | None = None) -> dict[str, Any]:
    return run_save_to_spotify(("doctor",), api_timeout=api_timeout)


def _show_uri(item: dict[str, Any]) -> str | None:
    for key in ("show_uri", "uri", "id"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def list_shows(*, api_timeout: str | None = None) -> tuple[SpotifyShow, ...]:
    payload = run_save_to_spotify(("shows",), api_timeout=api_timeout)
    values = payload.get("shows", [])
    if not isinstance(values, list):
        raise SpotifyProtocolError("save-to-spotify response has invalid shows")
    shows: list[SpotifyShow] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise SpotifyProtocolError(f"save-to-spotify show {index + 1} is invalid")
        uri = _show_uri(value)
        title = value.get("title") or value.get("name")
        if uri is None or not isinstance(title, str):
            raise SpotifyProtocolError(f"save-to-spotify show {index + 1} is missing fields")
        language = value.get("language")
        shows.append(SpotifyShow(uri, title, language if isinstance(language, str) else None))
    return tuple(shows)


def upload_episode(
    audio_path: Path,
    *,
    title: str,
    show_id: str | None = None,
    new_show: str | None = None,
    summary: str | None = None,
    image: Path | None = None,
    language: str | None = None,
    api_timeout: str | None = None,
) -> SpotifyUploadResult:
    args = ["upload", str(audio_path), "--title", title]
    if show_id is not None:
        args.extend(("--show-id", show_id))
    if new_show is not None:
        args.extend(("--new-show", new_show))
    if summary is not None:
        args.extend(("--summary", summary))
    if image is not None:
        args.extend(("--image", str(image)))
    if language is not None:
        args.extend(("--language", language))

    payload = run_save_to_spotify(args, api_timeout=api_timeout)
    episode_uri = payload.get("episode_uri")
    if not isinstance(episode_uri, str) or not episode_uri:
        raise SpotifyProtocolError("save-to-spotify response is missing episode_uri")
    status = payload.get("status")
    return SpotifyUploadResult(
        episode_uri=episode_uri,
        status=status if isinstance(status, str) else None,
    )


def episode_status(
    episode: str,
    *,
    wait: bool = False,
    wait_timeout: str | None = None,
    api_timeout: str | None = None,
) -> SpotifyReadinessResult:
    args = ["episodes", "status", episode]
    if wait:
        args.append("--wait")
        if wait_timeout is not None:
            args.append(wait_timeout)
    payload = run_save_to_spotify(args, api_timeout=api_timeout)
    readiness = payload.get("readiness")
    if not isinstance(readiness, str) or not readiness:
        raise SpotifyProtocolError("save-to-spotify response is missing readiness")
    return SpotifyReadinessResult(episode_uri=episode, readiness=readiness)


def wait_for_episode(
    episode_uri: str,
    *,
    timeout: str | None = None,
    api_timeout: str | None = None,
) -> SpotifyReadinessResult:
    return episode_status(
        episode_uri,
        wait=True,
        wait_timeout=timeout,
        api_timeout=api_timeout,
    )


def build_timeline(markers: tuple[dict[str, Any], ...], sample_rate: int) -> dict[str, Any]:
    if len(markers) < 2:
        raise SpotifyError("chapters require at least two markers")
    if sample_rate <= 0:
        raise SpotifyError("chapters require a positive render sample rate")

    items: list[dict[str, Any]] = []
    previous_offset = -1
    for index, marker in enumerate(markers):
        name = marker.get("name")
        offset = marker.get("sample_offset")
        if not isinstance(name, str) or not name:
            raise SpotifyError(f"chapter marker {index + 1} has no name")
        if not isinstance(offset, int) or offset < 0:
            raise SpotifyError(f"chapter marker {name!r} has an invalid sample offset")
        if index == 0 and offset != 0:
            raise SpotifyError("the first chapter marker must start at sample offset 0")
        if offset <= previous_offset:
            raise SpotifyError("chapter marker offsets must be strictly increasing")
        previous_offset = offset
        items.append(
            {
                "chapter": {
                    "title": name,
                    "start_time_ms": round(offset * 1000 / sample_rate),
                }
            }
        )
    return {"items": items}


def set_timeline(
    episode_uri: str,
    timeline_path: Path,
    *,
    api_timeout: str | None = None,
) -> None:
    run_save_to_spotify(
        (
            "timeline",
            "set",
            "--episode-id",
            episode_uri,
            "--from-file",
            str(timeline_path),
        ),
        api_timeout=api_timeout,
    )
