"""Versioned evidence for completed bounded renders."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .jsonutil import json_value

if TYPE_CHECKING:
    from .audio import RenderSummary
    from .plan import ReadioPlan


MANIFEST_SCHEMA = "readio.render-manifest.v1"


def manifest_path_for(output: Path) -> Path:
    """Return the deterministic sidecar path for a committed audio artifact."""
    return Path(f"{output}.readio.json")


def canonical_plan_json(plan: ReadioPlan) -> bytes:
    """Serialize a plan using the canonical representation used for its digest."""
    text = json.dumps(
        plan.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return text.encode("utf-8")


def plan_sha256(plan: ReadioPlan) -> str:
    """Return the SHA-256 digest of the canonical embedded plan."""
    return hashlib.sha256(canonical_plan_json(plan)).hexdigest()


def file_sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Hash a file's encoded bytes without loading the whole artifact."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _created_at(value: datetime | None) -> str:
    timestamp = value or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return (
        timestamp.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


def build_render_manifest(
    *,
    plan: ReadioPlan,
    summary: RenderSummary,
    output: Path,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Build evidence from the executed plan, render summary, and final artifact."""
    output_format = plan.output.format
    if output_format is None:
        raise ValueError("render manifest requires a resolved output format")
    if plan.output.encoder_backend is None:
        raise ValueError("render manifest requires a resolved encoder backend")

    return {
        "schema": MANIFEST_SCHEMA,
        "ok": True,
        "created_at": _created_at(created_at),
        "plan": {
            "sha256": plan_sha256(plan),
            "resolved": plan.to_dict(),
        },
        "result": {
            "output": {
                "path": str(output),
                "format": output_format,
                "encoder_backend": plan.output.encoder_backend,
                "byte_count": output.stat().st_size,
                "sha256": file_sha256(output),
            },
            "audio": {
                "sample_rate": summary.sample_rate,
                "sample_count": summary.sample_count,
                "channels": summary.channels,
                "duration_ms": round(summary.sample_count * 1000 / summary.sample_rate),
            },
            "document_metadata": json_value(summary.document_metadata),
            "markers": json_value(summary.markers),
        },
    }


def write_render_manifest(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically write a readable, deterministic UTF-8 manifest JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
