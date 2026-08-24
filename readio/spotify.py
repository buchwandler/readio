from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SpotifyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SpotifyUploadResult:
    episode_uri: str
    status: str | None


@dataclass(frozen=True, slots=True)
class SpotifyReadinessResult:
    episode_uri: str
    readiness: str


def _executable() -> str:
    executable = shutil.which("save-to-spotify")
    if executable is None:
        raise SpotifyError("save-to-spotify executable not found on PATH")
    return executable


def _run_json(command: list[str], *, invalid_message: str) -> dict[str, Any]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        if completed.returncode == 0:
            raise SpotifyError(invalid_message) from exc
        detail = completed.stderr.strip() or "save-to-spotify failed"
        raise SpotifyError(f"{detail} (exit code {completed.returncode})") from exc
    if not isinstance(payload, dict):
        raise SpotifyError(invalid_message)
    if completed.returncode != 0:
        detail = payload.get("error") or completed.stderr.strip() or "save-to-spotify failed"
        raise SpotifyError(f"{detail} (exit code {completed.returncode})")
    return payload


def upload_episode(
    audio_path: Path,
    *,
    title: str,
    show_id: str | None = None,
    new_show: str | None = None,
    summary: str | None = None,
    image: Path | None = None,
    language: str | None = None,
) -> SpotifyUploadResult:
    command = [_executable(), "--json", "upload", str(audio_path), "--title", title]
    if show_id is not None:
        command.extend(("--show-id", show_id))
    if new_show is not None:
        command.extend(("--new-show", new_show))
    if summary is not None:
        command.extend(("--summary", summary))
    if image is not None:
        command.extend(("--image", str(image)))
    if language is not None:
        command.extend(("--language", language))

    payload = _run_json(command, invalid_message="save-to-spotify returned invalid JSON")
    episode_uri = payload.get("episode_uri")
    if not isinstance(episode_uri, str) or not episode_uri:
        raise SpotifyError("save-to-spotify response is missing episode_uri")
    status = payload.get("status")
    return SpotifyUploadResult(
        episode_uri=episode_uri, status=status if isinstance(status, str) else None
    )


def wait_for_episode(
    episode_uri: str,
    *,
    timeout: str | None = None,
) -> SpotifyReadinessResult:
    command = [_executable(), "--json", "episodes", "status", episode_uri, "--wait"]
    if timeout is not None:
        command.append(timeout)
    payload = _run_json(command, invalid_message="save-to-spotify returned invalid JSON")
    readiness = payload.get("readiness")
    if not isinstance(readiness, str) or not readiness:
        raise SpotifyError("save-to-spotify response is missing readiness")
    return SpotifyReadinessResult(episode_uri=episode_uri, readiness=readiness)


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


def set_timeline(episode_uri: str, timeline_path: Path) -> None:
    command = [
        _executable(),
        "--json",
        "timeline",
        "set",
        "--episode-id",
        episode_uri,
        "--from-file",
        str(timeline_path),
    ]
    _run_json(command, invalid_message="save-to-spotify returned invalid JSON")
