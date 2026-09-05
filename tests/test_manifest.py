from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest

from readio.audio import RenderSummary
from readio.manifest import (
    MANIFEST_SCHEMA,
    build_render_manifest,
    canonical_plan_json,
    file_sha256,
    manifest_path_for,
    plan_sha256,
    write_render_manifest,
)


@dataclass(frozen=True)
class DocumentInfo:
    title: str
    path: Path


class Title(Enum):
    EPISODE = "Überblick"


def _plan(*, marker: str = "voice") -> SimpleNamespace:
    resolved = {
        "schema": "readio.plan.v1",
        "output": {
            "mode": "file",
            "format": "wav",
            "encoder_backend": "soundfile",
            "path": "/tmp/episode.wav",
            "path_origin": "explicit",
            "force": False,
        },
        "synthesis": {"voice": marker},
    }
    return SimpleNamespace(
        output=SimpleNamespace(format="wav", encoder_backend="soundfile"),
        to_dict=lambda: resolved,
    )


def test_manifest_sidecar_naming_preserves_audio_suffix() -> None:
    assert manifest_path_for(Path("episode.mp3")) == Path("episode.mp3.readio.json")
    assert manifest_path_for(Path("/path/a.b.m4a")) == Path("/path/a.b.m4a.readio.json")


def test_plan_digest_is_canonical_and_sensitive() -> None:
    first = SimpleNamespace(to_dict=lambda: {"b": 2, "a": "Überblick"})
    reordered = SimpleNamespace(to_dict=lambda: {"a": "Überblick", "b": 2})

    assert canonical_plan_json(first) == '{"a":"Überblick","b":2}'.encode()
    assert plan_sha256(first) == plan_sha256(reordered)
    assert plan_sha256(first) != plan_sha256(_plan(marker="other"))


def test_file_sha256_hashes_known_bytes(tmp_path: Path) -> None:
    output = tmp_path / "episode.wav"
    output.write_bytes(b"readio audio")

    assert file_sha256(output) == hashlib.sha256(b"readio audio").hexdigest()


def test_manifest_builder_contains_plan_result_and_json_safe_values(tmp_path: Path) -> None:
    output = tmp_path / "episode.wav"
    output.write_bytes(b"audio bytes")
    plan = _plan()
    summary = RenderSummary(
        sample_rate=24000,
        sample_count=60000,
        channels=1,
        document_metadata={
            "title": Title.EPISODE,
            "source": Path("notas/überblick.md"),
            "details": DocumentInfo("Título", Path("/tmp/episódio.md")),
        },
        markers=(
            {"label": "start", "sample_offset": 0},
            {"label": "end", "sample_offset": 59999},
        ),
    )

    manifest = build_render_manifest(
        plan=plan,
        summary=summary,
        output=output,
        created_at=datetime(2026, 9, 5, 18, 42, 31, 815000, tzinfo=timezone.utc),
    )

    assert manifest["schema"] == MANIFEST_SCHEMA
    assert manifest["ok"] is True
    assert manifest["created_at"] == "2026-09-05T18:42:31.815Z"
    assert manifest["plan"] == {"sha256": plan_sha256(plan), "resolved": plan.to_dict()}
    assert manifest["result"]["output"]["path"] == str(output)
    assert manifest["result"]["output"]["byte_count"] == len(b"audio bytes")
    assert manifest["result"]["output"]["sha256"] == file_sha256(output)
    assert manifest["result"]["audio"]["duration_ms"] == 2500
    assert manifest["result"]["document_metadata"] == {
        "title": "Überblick",
        "source": "notas/überblick.md",
        "details": {"title": "Título", "path": "/tmp/episódio.md"},
    }
    assert manifest["result"]["markers"] == list(summary.markers)
    json.dumps(manifest, ensure_ascii=False)


def test_atomic_manifest_writer_replaces_and_cleans_temporary_files(tmp_path: Path) -> None:
    path = tmp_path / "episode.wav.readio.json"
    write_render_manifest(path, {"schema": MANIFEST_SCHEMA, "title": "Überblick"})
    write_render_manifest(path, {"schema": MANIFEST_SCHEMA, "title": "Updated"})

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema": MANIFEST_SCHEMA,
        "title": "Updated",
    }
    assert path.read_bytes().endswith(b"\n")
    assert list(tmp_path.glob(f".{path.name}.*.tmp")) == []


def test_atomic_manifest_writer_does_not_leave_partial_final_file(tmp_path: Path) -> None:
    path = tmp_path / "episode.wav.readio.json"

    with pytest.raises(TypeError):
        write_render_manifest(path, {"invalid": object()})

    assert not path.exists()
    assert list(tmp_path.glob(f".{path.name}.*.tmp")) == []
