from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from readio.manifest import (
    RENDER_MANIFEST_SCHEMA_V2,
    canonical_plan_json,
    file_sha256,
    manifest_path_for,
    plan_sha256,
    write_render_manifest,
)


def test_manifest_sidecar_naming_preserves_audio_suffix() -> None:
    assert manifest_path_for(Path("episode.mp3")) == Path("episode.mp3.readio.json")
    assert manifest_path_for(Path("/path/a.b.m4a")) == Path("/path/a.b.m4a.readio.json")


def test_plan_digest_is_canonical_and_sensitive() -> None:
    first = SimpleNamespace(to_dict=lambda: {"b": 2, "a": "Überblick"})
    reordered = SimpleNamespace(to_dict=lambda: {"a": "Überblick", "b": 2})
    changed = SimpleNamespace(to_dict=lambda: {"a": "Überblick", "b": 3})

    assert canonical_plan_json(first) == '{"a":"Überblick","b":2}'.encode()
    assert plan_sha256(first) == plan_sha256(reordered)
    assert plan_sha256(first) != plan_sha256(changed)


def test_plan_canonical_json_preserves_explicit_tokenizer_policy_values() -> None:
    automatic = SimpleNamespace(
        to_dict=lambda: {
            "synthesis": {
                "lexicons": None,
                "g2p_fallback": None,
                "lexicon_data_policy": None,
                "language_detection": None,
                "detect_languages": None,
            }
        }
    )
    provider_only = SimpleNamespace(
        to_dict=lambda: {
            "synthesis": {
                "lexicons": [],
                "g2p_fallback": "espeak",
                "lexicon_data_policy": "installed-only",
                "language_detection": "auto",
                "detect_languages": ["de", "en"],
            }
        }
    )
    assert json.loads(canonical_plan_json(automatic))["synthesis"]["lexicons"] is None
    assert json.loads(canonical_plan_json(provider_only))["synthesis"]["lexicons"] == []
    assert plan_sha256(automatic) != plan_sha256(provider_only)


def test_file_sha256_hashes_known_bytes(tmp_path: Path) -> None:
    output = tmp_path / "episode.wav"
    output.write_bytes(b"readio audio")

    assert file_sha256(output) == hashlib.sha256(b"readio audio").hexdigest()


def test_atomic_manifest_writer_replaces_and_cleans_temporary_files(tmp_path: Path) -> None:
    path = tmp_path / "episode.wav.readio.json"
    write_render_manifest(path, {"schema": RENDER_MANIFEST_SCHEMA_V2, "title": "Überblick"})
    write_render_manifest(path, {"schema": RENDER_MANIFEST_SCHEMA_V2, "title": "Updated"})

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema": RENDER_MANIFEST_SCHEMA_V2,
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
