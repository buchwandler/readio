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
    from .plan import ReadioPlanV2


RENDER_MANIFEST_SCHEMA_V1 = "readio.render-manifest.v1"
RENDER_MANIFEST_SCHEMA_V2 = "readio.render-manifest.v2"
MANIFEST_SCHEMA = RENDER_MANIFEST_SCHEMA_V1


def manifest_path_for(output: Path) -> Path:
    """Return the deterministic sidecar path for a committed audio artifact."""
    return Path(f"{output}.readio.json")


def canonical_plan_json(plan: ReadioPlanV2) -> bytes:
    """Serialize a plan using the canonical representation used for its digest."""
    text = json.dumps(
        plan.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return text.encode("utf-8")


def plan_sha256(plan: ReadioPlanV2) -> str:
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
    plan: ReadioPlanV2,
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


def build_render_manifest_v2(
    *,
    plan_v2: Any,  # ReadioPlanV2
    summary: RenderSummary,
    output: Path,
    composition: Any | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Build v2 evidence for one completed bounded render."""
    output_format = plan_v2.output.format
    if output_format is None:
        raise ValueError("render manifest requires a resolved output format")
    if plan_v2.output.encoder_backend is None:
        raise ValueError("render manifest requires a resolved encoder backend")
    if plan_v2.render is None:
        raise ValueError("render manifest requires a resolved render section")
    semantic = plan_v2.semantic_plan
    if not semantic.plan_id or not semantic.sha256:
        raise ValueError("render manifest requires a concrete semantic plan identity")
    if not plan_v2.render.render_id:
        raise ValueError("render manifest requires a concrete render identity")

    plan_json = json.dumps(
        plan_v2.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    plan_hash = hashlib.sha256(plan_json).hexdigest()
    composition_sample_rate = (
        composition.sample_rate if composition is not None else summary.sample_rate
    )
    composition_sample_count = (
        int(composition.audio.shape[0]) if composition is not None else summary.sample_count
    )
    composition_markers = composition.markers if composition is not None else summary.markers
    composition_spans = composition.spans if composition is not None else ()
    composition_items = composition.items if composition is not None else ()

    return {
        "schema": RENDER_MANIFEST_SCHEMA_V2,
        "ok": True,
        "created_at": _created_at(created_at),
        "plan": {
            "sha256": plan_hash,
            "resolved": plan_v2.to_dict(),
        },
        "semantic_plan": {
            "format": semantic.format,
            "schema_version": semantic.schema_version,
            "plan_id": semantic.plan_id,
            "sha256": semantic.sha256,
        },
        "render": plan_v2.render.to_dict(),
        "composition": {
            "sample_rate": composition_sample_rate,
            "sample_count": composition_sample_count,
            "duration_ms": round(composition_sample_count * 1000 / composition_sample_rate),
            "items": json_value(composition_items),
            "markers": json_value(composition_markers),
            "spans": json_value(composition_spans),
        },
        "output": {
            "path": str(output),
            "format": output_format,
            "encoder_backend": plan_v2.output.encoder_backend,
            "byte_count": output.stat().st_size,
            "sha256": file_sha256(output),
        },
        "result": {
            "output": {
                "path": str(output),
                "format": output_format,
                "encoder_backend": plan_v2.output.encoder_backend,
                "byte_count": output.stat().st_size,
                "sha256": file_sha256(output),
            },
            "audio": {
                "sample_rate": composition_sample_rate,
                "sample_count": composition_sample_count,
                "channels": summary.channels,
                "duration_ms": round(composition_sample_count * 1000 / composition_sample_rate),
            },
            "document_metadata": json_value(summary.document_metadata),
            "markers": json_value(composition_markers),
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
