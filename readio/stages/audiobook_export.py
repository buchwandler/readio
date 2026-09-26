"""Preparation and identity for audiobook-specific M4B exports."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import soundfile as sf

from ..formats import ffmpeg_executable
from ..project import (
    Project,
    hash_file,
    project_lock,
    read_json,
)
from ..wave import atomic_audio_path
from .export import (
    export_id,
    normalize_bitrate,
    output_state_for,
    store_export_state,
    target_record,
)


class AudiobookExportError(ValueError):
    """A typed preparation failure with a stable public-facing error code."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True, slots=True)
class ResolvedAudiobookMetadata:
    title: str
    author: str | None
    cover: Path | None
    cover_sha256: str | None


@dataclass(frozen=True, slots=True)
class AudiobookChapterRange:
    scope_id: str
    title: str
    source_number: int | None
    start_sample: int
    end_sample: int


@dataclass(frozen=True, slots=True)
class PreparedAudiobookExport:
    master: Path
    master_sha256: str
    timeline_sha256: str
    sample_rate: int
    frames: int
    chapters: tuple[AudiobookChapterRange, ...]
    metadata: ResolvedAudiobookMetadata
    bitrate: str
    export_id: str


def build_audiobook_export_identity(
    *,
    master_sha256: str,
    timeline_sha256: str,
    title: str,
    author: str | None,
    cover_sha256: str | None,
    bitrate: str,
) -> str:
    return export_id(
        {
            "schema": "readio.audiobook-export.v1",
            "master_sha256": master_sha256,
            "timeline_sha256": timeline_sha256,
            "format": "m4b",
            "metadata": {
                "title": title,
                "author": author,
                "cover_sha256": cover_sha256,
            },
            "options": {"bitrate": bitrate},
        }
    )


def _validate_override(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"audiobook export {name} must not be empty")
    return normalized


def _resolve_metadata(
    project: Project,
    *,
    title: str | None,
    author: str | None,
    cover: Path | None,
) -> ResolvedAudiobookMetadata:
    document_metadata = project.load_document_index().metadata
    chosen_title = _validate_override(title, "title")
    if chosen_title is None:
        stored_title = document_metadata.get("title")
        chosen_title = stored_title.strip() if isinstance(stored_title, str) else ""
    if not chosen_title:
        chosen_title = project.manifest.name

    chosen_author = _validate_override(author, "author")
    if chosen_author is None:
        stored_authors = document_metadata.get("authors", ())
        authors = (
            [item.strip() for item in stored_authors if isinstance(item, str) and item.strip()]
            if isinstance(stored_authors, (list, tuple))
            else []
        )
        chosen_author = ", ".join(authors) or None

    resolved_cover: Path | None = None
    cover_digest: str | None = None
    if cover is not None:
        resolved_cover = Path(cover).expanduser().resolve()
        if not resolved_cover.is_file():
            raise AudiobookExportError(
                f"audiobook cover is not a regular file: {resolved_cover}",
                code="audiobook.export.cover_not_found",
            )
        try:
            with resolved_cover.open("rb") as stream:
                signature = stream.read(8)
        except OSError as error:
            raise AudiobookExportError(
                f"cannot read audiobook cover: {resolved_cover}",
                code="audiobook.export.cover_not_found",
            ) from error
        if not (signature.startswith(b"\xff\xd8\xff") or signature == b"\x89PNG\r\n\x1a\n"):
            raise AudiobookExportError(
                "audiobook cover must be a JPEG or PNG image",
                code="audiobook.export.cover_unsupported",
            )
        cover_digest = hash_file(resolved_cover)
    return ResolvedAudiobookMetadata(chosen_title, chosen_author, resolved_cover, cover_digest)


def _chapter_ranges(raw_chapters: Any, frames: int) -> tuple[AudiobookChapterRange, ...]:
    if not isinstance(raw_chapters, list) or not raw_chapters:
        raise AudiobookExportError(
            "audiobook composition timeline contains no chapter rows; recompose the project",
            code="audiobook.export.invalid_chapters",
        )
    starts: list[tuple[str, str, int | None, int]] = []
    for index, row in enumerate(raw_chapters, 1):
        if not isinstance(row, dict):
            raise AudiobookExportError(
                f"audiobook chapter row {index} is invalid; recompose the project",
                code="audiobook.export.invalid_chapters",
            )
        scope_id = row.get("scope_id")
        start = row.get("start_sample")
        if not isinstance(scope_id, str) or not scope_id.strip():
            raise AudiobookExportError(
                f"audiobook chapter row {index} has no scope id; recompose the project",
                code="audiobook.export.invalid_chapters",
            )
        if not isinstance(start, int) or isinstance(start, bool):
            raise AudiobookExportError(
                f"audiobook chapter row {index} has an invalid start sample; recompose the project",
                code="audiobook.export.invalid_chapters",
            )
        title = row.get("title")
        title = title.strip() if isinstance(title, str) and title.strip() else f"Chapter {index}"
        source_number = row.get("source_number")
        if not isinstance(source_number, int) or isinstance(source_number, bool):
            source_number = None
        starts.append((scope_id, title, source_number, start))

    if (
        starts[0][3] < 0
        or any(start < 0 or start >= frames for _, _, _, start in starts)
        or any(starts[index][3] >= starts[index + 1][3] for index in range(len(starts) - 1))
    ):
        raise AudiobookExportError(
            "audiobook chapter starts must increase strictly before the final frame; recompose the project",
            code="audiobook.export.invalid_chapters",
        )
    return tuple(
        AudiobookChapterRange(
            scope_id=scope_id,
            title=title,
            source_number=source_number,
            start_sample=start,
            end_sample=starts[index + 1][3] if index + 1 < len(starts) else frames,
        )
        for index, (scope_id, title, source_number, start) in enumerate(starts)
    )


def prepare_audiobook_export(
    project: Project,
    *,
    title: str | None = None,
    author: str | None = None,
    cover: Path | None = None,
    bitrate: str | None = None,
) -> PreparedAudiobookExport:
    if project.manifest.kind != "audiobook":
        raise AudiobookExportError(
            "M4B export requires an audiobook project created from an EPUB",
            code="audiobook.export.not_audiobook",
        )
    master = project.paths["composition_master"]
    state_path = project.paths["composition_state"]
    timeline_path = project.paths["composition_timeline"]
    if not master.is_file() or not state_path.is_file():
        raise AudiobookExportError(
            "audiobook composition is missing; run readio compose before exporting",
            code="audiobook.export.composition_missing",
        )
    if not timeline_path.is_file():
        raise AudiobookExportError(
            "audiobook composition timeline is missing; recompose the project",
            code="audiobook.export.timeline_missing",
        )
    try:
        state = read_json(state_path)
        timeline = read_json(timeline_path)
        if (
            state.get("format") != "readio.composition-state"
            or timeline.get("format") != "readio.composition-timeline"
        ):
            raise ValueError("composition artifact has an unexpected format")
    except (OSError, ValueError) as error:
        raise AudiobookExportError(
            "audiobook composition artifacts are unreadable; recompose the project",
            code="audiobook.export.timeline_stale",
        ) from error

    master_sha = hash_file(master)
    timeline_sha = hash_file(timeline_path)
    if state.get("master_sha256") != master_sha or state.get("timeline_sha256") != timeline_sha:
        raise AudiobookExportError(
            "audiobook composition master or timeline changed after composition; recompose the project",
            code="audiobook.export.timeline_stale",
        )
    composition_id = state.get("composition_id")
    if (
        not isinstance(composition_id, str)
        or not composition_id
        or timeline.get("composition_id") != composition_id
    ):
        raise AudiobookExportError(
            "audiobook timeline does not belong to the current composition; recompose the project",
            code="audiobook.export.timeline_stale",
        )
    sample_rate = state.get("sample_rate")
    frames = state.get("frames")
    if (
        not isinstance(sample_rate, int)
        or isinstance(sample_rate, bool)
        or sample_rate <= 0
        or timeline.get("sample_rate") != sample_rate
    ):
        raise AudiobookExportError(
            "audiobook timeline sample rate does not match the composition; recompose the project",
            code="audiobook.export.timeline_stale",
        )
    if not isinstance(frames, int) or isinstance(frames, bool) or frames <= 0:
        raise AudiobookExportError(
            "audiobook composition has no positive audio duration; recompose the project",
            code="audiobook.export.invalid_chapters",
        )
    try:
        master_info = sf.info(master)
    except (OSError, RuntimeError, ValueError) as error:
        raise AudiobookExportError(
            "audiobook composition master is unreadable; recompose the project",
            code="audiobook.export.composition_missing",
        ) from error
    if master_info.samplerate != sample_rate or master_info.frames != frames:
        raise AudiobookExportError(
            "audiobook composition master does not match its state; recompose the project",
            code="audiobook.export.timeline_stale",
        )

    chapters = _chapter_ranges(timeline.get("chapters"), frames)
    resolved_metadata = _resolve_metadata(project, title=title, author=author, cover=cover)
    normalized_bitrate = normalize_bitrate(bitrate if bitrate is not None else "192k")
    identity = build_audiobook_export_identity(
        master_sha256=master_sha,
        timeline_sha256=timeline_sha,
        title=resolved_metadata.title,
        author=resolved_metadata.author,
        cover_sha256=resolved_metadata.cover_sha256,
        bitrate=normalized_bitrate,
    )
    return PreparedAudiobookExport(
        master=master,
        master_sha256=master_sha,
        timeline_sha256=timeline_sha,
        sample_rate=sample_rate,
        frames=frames,
        chapters=chapters,
        metadata=resolved_metadata,
        bitrate=normalized_bitrate,
        export_id=identity,
    )


def escape_ffmetadata_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    escaped = escaped.replace("=", "\\=").replace(";", "\\;").replace("#", "\\#")
    return escaped.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def ffmetadata_text(prepared: PreparedAudiobookExport) -> str:
    lines = [";FFMETADATA1", f"title={escape_ffmetadata_value(prepared.metadata.title)}"]
    if prepared.metadata.author:
        lines.append(f"artist={escape_ffmetadata_value(prepared.metadata.author)}")
    lines.append("")
    for chapter in prepared.chapters:
        lines.extend(
            (
                "[CHAPTER]",
                f"TIMEBASE=1/{prepared.sample_rate}",
                f"START={chapter.start_sample}",
                f"END={chapter.end_sample}",
                f"title={escape_ffmetadata_value(chapter.title)}",
                "",
            )
        )
    return "\n".join(lines)


def build_m4b_ffmpeg_command(
    executable: str,
    prepared: PreparedAudiobookExport,
    ffmetadata_path: Path,
    output: Path,
    *,
    bitrate: str,
    cover: Path | None,
) -> list[str]:
    command = [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(prepared.master),
        "-f",
        "ffmetadata",
        "-i",
        str(ffmetadata_path),
    ]
    cover_input_index = 2
    if cover is not None:
        command.extend(("-i", str(cover)))
    command.extend(("-map", "0:a:0", "-map_metadata", "1", "-map_chapters", "1"))
    if cover is not None:
        command.extend(("-map", f"{cover_input_index}:v:0"))
    command.extend(("-c:a", "aac", "-b:a", bitrate))
    if cover is not None:
        command.extend(("-c:v", "copy", "-disposition:v:0", "attached_pic"))
    command.extend(("-movflags", "+faststart", "-f", "ipod", str(output)))
    return command


def _stderr_tail(stderr: str) -> str:
    lines = stderr.splitlines()
    tail = "\n".join(line.strip() for line in lines[-5:] if line.strip())
    sanitized = "".join(
        character for character in tail if character in "\n\t" or ord(character) >= 32
    )
    return sanitized[-2000:]


def _write_ffmetadata_file(target: Path, prepared: PreparedAudiobookExport) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{target.stem}.", suffix=".ffmeta", dir=target.parent
    )
    path = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(ffmetadata_text(prepared))
            stream.flush()
        return path
    except OSError as error:
        path.unlink(missing_ok=True)
        raise AudiobookExportError(
            f"could not prepare audiobook chapter metadata: {error}",
            code="audiobook.export.encode_failed",
        ) from error


def _verify_m4b_output(
    output: Path,
    prepared: PreparedAudiobookExport,
    ffprobe: str | None,
) -> None:
    if not output.is_file() or output.stat().st_size <= 0:
        raise AudiobookExportError(
            "FFmpeg did not produce a non-empty M4B output",
            code="audiobook.export.encode_failed",
        )
    if ffprobe is None:
        return
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_streams",
        "-show_chapters",
        "-show_format",
        "-of",
        "json",
        str(output),
    ]
    try:
        probe = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as error:
        raise AudiobookExportError(
            f"failed to start ffprobe for M4B verification: {error}",
            code="audiobook.export.encode_failed",
        ) from error
    if probe.returncode != 0:
        tail = _stderr_tail(probe.stderr or "")
        raise AudiobookExportError(
            "ffprobe could not verify the produced M4B",
            code="audiobook.export.encode_failed",
            details={"stderr_tail": tail} if tail else None,
        )
    try:
        payload = json.loads(probe.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise AudiobookExportError(
            "ffprobe returned invalid M4B verification data",
            code="audiobook.export.encode_failed",
        ) from error
    streams = payload.get("streams", [])
    audio_streams = (
        [item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"]
        if isinstance(streams, list)
        else []
    )
    chapters = payload.get("chapters", [])
    if (
        len(audio_streams) != 1
        or not isinstance(chapters, list)
        or len(chapters) != len(prepared.chapters)
    ):
        raise AudiobookExportError(
            "M4B verification found an unexpected audio stream or chapter count",
            code="audiobook.export.encode_failed",
        )
    chapter_titles = [
        str(item.get("tags", {}).get("title", ""))
        for item in chapters
        if isinstance(item, dict) and isinstance(item.get("tags", {}), dict)
    ]
    if chapter_titles != [chapter.title for chapter in prepared.chapters]:
        raise AudiobookExportError(
            "M4B chapter titles or order do not match the composition timeline",
            code="audiobook.export.encode_failed",
        )
    format_info = payload.get("format", {})
    tags = format_info.get("tags", {}) if isinstance(format_info, dict) else {}
    normalized_tags = (
        {str(key).lower(): str(value) for key, value in tags.items()}
        if isinstance(tags, dict)
        else {}
    )
    if normalized_tags.get("title") != prepared.metadata.title:
        raise AudiobookExportError(
            "M4B title metadata could not be verified",
            code="audiobook.export.encode_failed",
        )
    if prepared.metadata.author and normalized_tags.get("artist") != prepared.metadata.author:
        raise AudiobookExportError(
            "M4B artist metadata could not be verified",
            code="audiobook.export.encode_failed",
        )
    try:
        duration = float(format_info.get("duration", 0))
    except (TypeError, ValueError):
        duration = 0
    if duration <= 0:
        raise AudiobookExportError(
            "M4B verification found a zero or invalid duration",
            code="audiobook.export.encode_failed",
        )
    if prepared.metadata.cover is not None and not any(
        isinstance(item, dict)
        and isinstance(item.get("disposition"), dict)
        and item["disposition"].get("attached_pic") == 1
        for item in streams
    ):
        raise AudiobookExportError(
            "M4B cover was not muxed as an attached picture",
            code="audiobook.export.encode_failed",
        )


def _audiobook_target(project: Project, output: Path | None) -> Path:
    target = output or project.root / "output" / f"{project.manifest.name}.m4b"
    target = Path(target).expanduser()
    if not target.is_absolute():
        target = project.root / target
    if not target.suffix:
        target = target.with_name(f"{target.name}.m4b")
    elif target.suffix.lower() != ".m4b":
        raise ValueError("audiobook export output must use the .m4b extension")
    return target.resolve()


def is_audiobook_export_current(
    project: Project, target: Path, prepared: PreparedAudiobookExport
) -> bool:
    if not target.is_file():
        return False
    state = output_state_for(project, target)
    return (
        state is not None
        and state.get("format") == "readio.audiobook-export-state"
        and state.get("export_id") == prepared.export_id
        and state.get("path") == target_record(project, target)
        and state.get("output_sha256") == hash_file(target)
    )


def export_audiobook_project(
    project: Project,
    *,
    output: Path | None = None,
    title: str | None = None,
    author: str | None = None,
    cover: Path | None = None,
    bitrate: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    with project_lock(project, operation="audiobooks.export"):
        prepared = prepare_audiobook_export(
            project, title=title, author=author, cover=cover, bitrate=bitrate
        )
        target = _audiobook_target(project, output)
        target.parent.mkdir(parents=True, exist_ok=True)
        previous = output_state_for(project, target)
        tracked_unchanged = (
            (
                previous is not None
                and previous.get("format")
                in {
                    "readio.export-state",
                    "readio.audiobook-export-state",
                }
                and previous.get("path") == target_record(project, target)
                and previous.get("output_sha256") == hash_file(target)
            )
            if target.is_file()
            else False
        )
        if target.exists() and not force and not tracked_unchanged:
            raise AudiobookExportError(
                f"output already exists and is not an unchanged Readio export: {target}; use force=True to replace it",
                code="audiobook.export.output_exists",
            )
        if (
            not force
            and tracked_unchanged
            and is_audiobook_export_current(project, target, prepared)
        ):
            assert previous is not None
            return {
                "export_id": prepared.export_id,
                "path": target,
                "format": "m4b",
                "output_sha256": previous["output_sha256"],
                "chapter_count": len(prepared.chapters),
            }
        executable = ffmpeg_executable()
        if executable is None:
            raise AudiobookExportError(
                "M4B output requires FFmpeg; install ffmpeg and ensure it is on PATH",
                code="audiobook.export.ffmpeg_missing",
            )
        metadata_path = _write_ffmetadata_file(target, prepared)
        try:
            with atomic_audio_path(target, force=force or tracked_unchanged) as temporary:
                command = build_m4b_ffmpeg_command(
                    executable,
                    prepared,
                    metadata_path,
                    temporary,
                    bitrate=prepared.bitrate,
                    cover=prepared.metadata.cover,
                )
                try:
                    result = subprocess.run(command, capture_output=True, text=True, check=False)
                except OSError as error:
                    raise AudiobookExportError(
                        f"failed to start FFmpeg for M4B: {error}",
                        code="audiobook.export.encode_failed",
                    ) from error
                if result.returncode != 0:
                    tail = _stderr_tail(result.stderr or "")
                    raise AudiobookExportError(
                        "FFmpeg failed to encode M4B" + (f": {tail}" if tail else ""),
                        code="audiobook.export.encode_failed",
                        details={"stderr_tail": tail} if tail else None,
                    )
                _verify_m4b_output(temporary, prepared, shutil.which("ffprobe"))
        finally:
            metadata_path.unlink(missing_ok=True)
        state = {
            "format": "readio.audiobook-export-state",
            "schema_version": 1,
            "export_id": prepared.export_id,
            "master_sha256": prepared.master_sha256,
            "timeline_sha256": prepared.timeline_sha256,
            "audio_format": "m4b",
            "options": {"bitrate": prepared.bitrate},
            "metadata": {
                "title": prepared.metadata.title,
                "author": prepared.metadata.author,
                "cover_sha256": prepared.metadata.cover_sha256,
            },
            "path": target_record(project, target),
            "output_sha256": hash_file(target),
        }
        store_export_state(project, target, state)
        return {
            "export_id": state["export_id"],
            "path": target,
            "format": "m4b",
            "output_sha256": state["output_sha256"],
            "chapter_count": len(prepared.chapters),
        }


__all__ = [
    "AudiobookChapterRange",
    "AudiobookExportError",
    "PreparedAudiobookExport",
    "ResolvedAudiobookMetadata",
    "build_audiobook_export_identity",
    "build_m4b_ffmpeg_command",
    "escape_ffmetadata_value",
    "export_audiobook_project",
    "ffmetadata_text",
    "is_audiobook_export_current",
    "prepare_audiobook_export",
]
