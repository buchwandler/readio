"""Incremental, content-addressed project synthesis stage."""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import soundfile as sf

from ..plan import InputRequest, OutputRequest, PlanRequest, SynthesisRequest, resolve_execution_v2
from ..project import Project, atomic_write_json, canonical_json, hash_file, project_lock, read_json
from ..selection import resolve_unit_selection
from .planning import load_scope_plan
from .speech_identity import segment_speech_hash, segment_synthesis_key


@dataclass(frozen=True, slots=True)
class SynthesisEvent:
    """Engine-neutral lifecycle event emitted by project synthesis."""

    kind: str
    scope_id: str | None = None
    unit_id: str | None = None
    unit_index: int | None = None
    segment_id: str | None = None
    segment_index: int | None = None
    completed: int | None = None
    total: int | None = None
    text: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SynthesisProfile:
    profile_id: str
    payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "readio.synthesis-profile",
            "schema_version": 2,
            "profile_id": self.profile_id,
            **dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class SynthesisArtifact:
    """A validated canonical speech artifact and its active segment view."""

    unit_id: str
    unit_index: int
    content_hash: str
    synthesis_key: str
    path: Path
    cache_path: Path
    audio_sha256: str
    sample_rate: int
    channels: int
    frames: int
    markers: tuple[Mapping[str, Any], ...] = ()
    speech_hash: str | None = None
    segment_id_value: str | None = None
    segment_index_value: int | None = None
    sidecar_path: Path | None = None

    @property
    def segment_id(self) -> str:
        return self.segment_id_value or self.unit_id

    @property
    def segment_index(self) -> int:
        return self.segment_index_value if self.segment_index_value is not None else self.unit_index

    def to_dict(self, root: Path) -> dict[str, Any]:
        speech_hash = self.speech_hash or self.content_hash
        data: dict[str, Any] = {
            "scope_id": "document",
            "segment_id": self.segment_id,
            "segment_index": self.segment_index,
            "speech_hash": speech_hash,
            "synthesis_key": self.synthesis_key,
            "path": self.path.relative_to(root).as_posix(),
            "cache_path": self.cache_path.relative_to(root).as_posix(),
            "audio_sha256": self.audio_sha256,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "frames": self.frames,
            "markers": list(self.markers),
        }
        if self.sidecar_path is not None:
            data["sidecar_path"] = self.sidecar_path.relative_to(root).as_posix()
        return data


def synthesis_profile_id(payload: Mapping[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(payload)).hexdigest()}"


def unit_synthesis_key(content_hash: str, profile_id: str) -> str:
    """Compatibility helper for callers of the former unit cache API."""
    payload = {
        "schema": "readio.synthesis-unit.v1",
        "content_hash": content_hash,
        "profile_id": profile_id,
    }
    return f"sha256:{hashlib.sha256(canonical_json(payload)).hexdigest()}"


def _safe_key(key: str) -> str:
    return key.replace(":", "-")


def _fallback_canonical_identity(selection: Any, adapter: Any) -> dict[str, Any]:
    editorial = {
        "speed",
        "rate",
        "volume",
        "pitch",
        "emphasis",
        "pause_mode",
        "sentence_silence",
        "pause_sentence",
        "pause_paragraph",
        "fade_in",
        "fade_out",
        "target_lufs",
        "true_peak_ceiling_dbtp",
    }
    return {
        "engine": selection.engine,
        "engine_version": adapter.version(),
        "target_id": selection.target_id,
        "language": selection.language,
        "voice": selection.voice,
        "speaker": selection.speaker,
        "options": {
            key: value for key, value in selection.options.items() if key not in editorial
        },
        "metadata": dict(selection.metadata),
    }


def _profile_from_selection(adapter: Any, selection: Any) -> SynthesisProfile:
    identity_method = getattr(adapter, "canonical_synthesis_identity", None)
    identity = (
        dict(identity_method(selection))
        if callable(identity_method)
        else _fallback_canonical_identity(selection, adapter)
    )
    composition = {
        key: value
        for key, value in selection.options.items()
        if key in {"speed", "rate", "pitch", "volume", "emphasis"} and value is not None
    }
    identity_payload = {
        "schema": "readio.synthesis-profile.v2",
        "canonical": identity,
    }
    payload: dict[str, Any] = {
        **identity_payload,
        "composition": composition,
    }
    return SynthesisProfile(synthesis_profile_id(identity_payload), payload)


def _valid_audio(path: Path, expected_sha: str | None = None) -> tuple[int, int, int, str] | None:
    if not path.is_file():
        return None
    try:
        info = sf.info(path)
        digest = hash_file(path)
    except (OSError, RuntimeError, ValueError):
        return None
    if expected_sha is not None and digest != expected_sha:
        return None
    if info.frames <= 0 or info.samplerate <= 0:
        return None
    return int(info.samplerate), int(info.channels), int(info.frames), digest


def _link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{secrets.token_hex(6)}")
    temporary.unlink(missing_ok=True)
    try:
        try:
            os.link(source, temporary)
        except (AttributeError, OSError):
            shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _request_for_project(project: Project, cfg: Any, request: PlanRequest | None) -> PlanRequest:
    if request is not None:
        return request
    reader = cfg.reader
    synthesis = SynthesisRequest(
        language=reader.lang,
        voice=reader.voice,
        engine=reader.engine,
        speed=reader.speed,
        pause_mode=reader.pause_mode,
        unit=reader.unit,
    )
    return PlanRequest(
        operation="render",
        input=InputRequest(document=project.document(), selector="all", source_kind="file"),
        synthesis=synthesis,
        output=OutputRequest(mode="file", requested_format="wav", force=True),
    )


def _resolve_profile(
    project: Project, cfg: Any, request: PlanRequest
) -> tuple[Any, Any, SynthesisProfile]:
    document = project.document()
    if document.format == "ssmd" or project.manifest.source_format == "ssmd":
        from ..ssmd import document_voice_bindings

        if document.format != "ssmd":
            document = replace(document, format="ssmd")
        local_bindings = document_voice_bindings(document.text).get(cfg.ssmd.voice_provider, {})
        merged_bindings = {**local_bindings, **dict(request.voice_bindings)}
        request = replace(
            request,
            input=replace(request.input, document=document),
            voice_bindings=merged_bindings,
        )
    resolved = resolve_execution_v2(cfg, request)
    if not resolved.plan.ok or resolved.selection is None:
        diagnostics = "; ".join(item.message for item in resolved.plan.diagnostics)
        raise ValueError(f"cannot resolve synthesis profile: {diagnostics}")
    from ..engines.registry import get_engine

    adapter = get_engine(resolved.selection.engine)
    return resolved, adapter, _profile_from_selection(adapter, resolved.selection)


def _emit(on_event: Callable[[SynthesisEvent], None] | None, event: SynthesisEvent) -> None:
    if on_event is not None:
        on_event(event)


def _unit_preview(plan: Any, unit: Any) -> str | None:
    texts = getattr(plan, "texts", {})
    spoken = texts.get("spoken", "") if isinstance(texts, Mapping) else getattr(texts, "spoken", "")
    start = int(getattr(unit, "spoken_start", 0))
    end = int(getattr(unit, "spoken_end", start))
    text = " ".join(str(spoken[start:end]).split())
    return text[:117] + "..." if len(text) > 120 else text


def _segment_preview(segment: Any) -> str:
    text = " ".join(str(getattr(segment, "text", "")).split())
    return text[:117] + "..." if len(text) > 120 else text


def _result_details(result: Any) -> dict[str, Any]:
    metadata = getattr(result, "metadata", {}) or {}
    details: dict[str, Any] = {}
    if isinstance(metadata, Mapping):
        for key in ("voice", "role", "speaker", "warnings", "fallback_reason"):
            value = metadata.get(key)
            if value is not None and isinstance(value, (str, int, float, bool, list, tuple)):
                details[key] = list(value) if isinstance(value, tuple) else value
    for key in ("voice", "role"):
        value = getattr(result, key, None)
        if value is not None and key not in details and isinstance(value, (str, int, float, bool)):
            details[key] = value
    return details


def _write_cache_artifact(
    project: Project,
    item: Mapping[str, Any],
    result: Any,
    profile: SynthesisProfile,
) -> tuple[int, int, int, str, Path]:
    cache_path = item["cache_path"]
    sidecar_path = item["sidecar_path"]
    temporary = cache_path.with_name(f".tmp-{secrets.token_hex(8)}.wav")
    try:
        sf.write(temporary, result.audio, int(result.sample_rate), subtype="PCM_16")
        checked = _valid_audio(temporary)
        if checked is None:
            raise ValueError(f"engine produced invalid audio for {item['segment_id']}")
        rate, channels, frames, digest = checked
        os.replace(temporary, cache_path)
        atomic_write_json(
            sidecar_path,
            {
                "format": "readio.synthesis-artifact",
                "schema_version": 2,
                "segment_id_at_creation": item["segment_id"],
                "speech_hash": item["speech_hash"],
                "synthesis_key": item["synthesis_key"],
                "profile_id": profile.profile_id,
                "audio_sha256": digest,
                "sample_rate": rate,
                "channels": channels,
                "frames": frames,
            },
        )
        return rate, channels, frames, digest, sidecar_path
    finally:
        temporary.unlink(missing_ok=True)


def _render_missing(
    project: Project,
    plan: Any,
    adapter: Any,
    selection: Any,
    stale: list[dict[str, Any]],
    profile: SynthesisProfile,
    *,
    on_event: Callable[[SynthesisEvent], None] | None = None,
    scope_id: str = "document",
) -> dict[int, Mapping[str, Any]]:
    if not stale:
        return {}
    details_by_index: dict[int, Mapping[str, Any]] = {}
    total = len(stale)
    _emit(on_event, SynthesisEvent("engine_open_started", scope_id=scope_id, total=total))
    engine_started = time.monotonic()
    with adapter.open(selection) as session:
        _emit(
            on_event,
            SynthesisEvent(
                "engine_open_finished",
                scope_id=scope_id,
                total=total,
                details={"elapsed_ms": round((time.monotonic() - engine_started) * 1000, 3)},
            ),
        )
        use_segments = callable(getattr(session, "prepare_segments", None))
        prepare_method = session.prepare_segments if use_segments else session.prepare_plan
        _emit(on_event, SynthesisEvent("prepare_started", scope_id=scope_id, total=total))
        prepare_started = time.monotonic()
        with prepare_method(plan, options=selection.options) as prepared:
            _emit(
                on_event,
                SynthesisEvent(
                    "prepare_finished",
                    scope_id=scope_id,
                    total=total,
                    details={"elapsed_ms": round((time.monotonic() - prepare_started) * 1000, 3)},
                ),
            )
            render_kwargs = (
                {"segment_ids": tuple(item["segment_id"] for item in stale)}
                if use_segments
                else {"indices": tuple(item["unit"].index for item in stale)}
            )
            render_iter = iter(prepared.render(**render_kwargs))
            try:
                for completed, item in enumerate(stale, 1):
                    segment = item["segment"]
                    unit = item["unit"]
                    event_kind = "segment_started" if use_segments else "unit_started"
                    _emit(
                        on_event,
                        SynthesisEvent(
                            event_kind,
                            scope_id=scope_id,
                            unit_id=unit.id,
                            unit_index=int(unit.index),
                            segment_id=item["segment_id"],
                            segment_index=item["segment_index"],
                            completed=completed - 1,
                            total=total,
                            text=_segment_preview(segment) if use_segments else _unit_preview(plan, unit),
                            details={"segment_ids": [item["segment_id"]]},
                        ),
                    )
                    render_started = time.monotonic()
                    try:
                        result = next(render_iter)
                    except StopIteration as exc:
                        label = "segment" if use_segments else "plan unit"
                        raise ValueError(
                            f"engine stopped rendering before {label} "
                            f"{item['segment_id'] if use_segments else unit.id} "
                            f"(index {item['render_index'] if use_segments else unit.index})"
                        ) from exc
                    try:
                        result_segment_id = getattr(result, "segment_id", None)
                        if use_segments and result_segment_id is not None:
                            if str(result_segment_id) != item["segment_id"]:
                                raise ValueError(
                                    f"engine returned segment {result_segment_id}; "
                                    f"expected {item['segment_id']}"
                                )
                        else:
                            descriptor = getattr(result, "descriptor", None)
                            index = int(
                                getattr(
                                    result,
                                    "index",
                                    getattr(
                                        result,
                                        "segment_index",
                                        getattr(
                                            result,
                                            "unit_index",
                                            getattr(descriptor, "index", -1),
                                        ),
                                    ),
                                )
                            )
                            if index < 0:
                                metadata = getattr(result, "metadata", {}) or {}
                                index = int(metadata.get("segment_index", metadata.get("unit_index", -1)))
                            expected = item["render_index"] if use_segments else int(unit.index)
                            if index != expected:
                                kind = "segment" if use_segments else "plan unit"
                                raise ValueError(f"engine returned {kind} index {index}; expected {expected}")
                        rate, channels, frames, digest, sidecar = _write_cache_artifact(
                            project, item, result, profile
                        )
                        item["rendered"] = (rate, channels, frames, digest, sidecar)
                        details = _result_details(result)
                        details_by_index[item["segment_index"]] = details
                        finished_kind = "segment_finished" if use_segments else "unit_finished"
                        _emit(
                            on_event,
                            SynthesisEvent(
                                finished_kind,
                                scope_id=scope_id,
                                unit_id=unit.id,
                                unit_index=int(unit.index),
                                segment_id=item["segment_id"],
                                segment_index=item["segment_index"],
                                completed=completed,
                                total=total,
                                text=_segment_preview(segment) if use_segments else _unit_preview(plan, unit),
                                details={
                                    **details,
                                    "segment_ids": [item["segment_id"]],
                                    "render_ms": round((time.monotonic() - render_started) * 1000, 3),
                                },
                            ),
                        )
                    finally:
                        release = getattr(result, "release_audio", None)
                        if callable(release):
                            release()
            finally:
                close = getattr(render_iter, "close", None)
                if callable(close):
                    close()
    return details_by_index


def _artifact_from_item(project: Project, item: Mapping[str, Any]) -> SynthesisArtifact | None:
    checked = _valid_audio(item["cache_path"])
    if checked is None:
        return None
    rate, channels, frames, digest = checked
    try:
        sidecar = read_json(item["sidecar_path"])
    except (OSError, ValueError):
        return None
    if (
        sidecar.get("speech_hash") != item["speech_hash"]
        or sidecar.get("synthesis_key") != item["synthesis_key"]
        or sidecar.get("profile_id") != item["profile_id"]
        or sidecar.get("audio_sha256") != digest
    ):
        return None
    return SynthesisArtifact(
        unit_id=item["unit"].id,
        unit_index=int(item["unit"].index),
        content_hash=item["speech_hash"],
        synthesis_key=item["synthesis_key"],
        path=item["path"],
        cache_path=item["cache_path"],
        audio_sha256=digest,
        sample_rate=rate,
        channels=channels,
        frames=frames,
        speech_hash=item["speech_hash"],
        segment_id_value=item["segment_id"],
        segment_index_value=item["segment_index"],
        sidecar_path=item["sidecar_path"],
    )


def synthesize_project(
    project: Project,
    cfg: Any,
    *,
    request: PlanRequest | None = None,
    selector: str = "all",
    activate: bool = True,
    on_event: Callable[[SynthesisEvent], None] | None = None,
) -> dict[str, Any]:
    """Synthesize selected stale segments, loading the engine only when needed."""
    started_at = time.monotonic()
    started_wall = datetime.now(timezone.utc).isoformat()
    with project_lock(project, operation="synth"):
        plan = load_scope_plan(project)
        unit_selection = resolve_unit_selection(plan, selector)
        request = _request_for_project(project, cfg, request)
        resolved, adapter, profile = _resolve_profile(project, cfg, request)
        selected_indices = set(unit_selection.unit_indices)
        selected_units = [unit for unit in plan.units if int(unit.index) in selected_indices]
        units_by_segment: dict[str, Any] = {}
        for unit in selected_units:
            for segment_id in unit.segment_ids:
                units_by_segment.setdefault(str(segment_id), unit)
        segments_by_id = {str(segment.id): segment for segment in plan.segments}
        selected_segments = [
            segments_by_id[segment_id]
            for segment_id in unit_selection.segment_ids
            if segment_id in segments_by_id
        ]
        if not selected_segments:
            raise ValueError("selected units contain no readable plan segments")
        _emit(
            on_event,
            SynthesisEvent(
                "profile_resolved",
                scope_id="document",
                total=len(selected_segments),
                details={
                    "project": str(project.root),
                    "source": str(project.manifest.source_path),
                    "source_format": project.manifest.source_format,
                    "plan_id": plan.plan_id,
                    "selected_units": len(unit_selection.unit_indices),
                    "selected_segments": len(selected_segments),
                    "profile_id": profile.profile_id,
                    "engine": profile.payload.get("canonical", {}).get("engine"),
                    "engine_version": profile.payload.get("canonical", {}).get("engine_version"),
                    "target": {
                        "id": resolved.selection.target_id,
                        "voice": resolved.selection.voice,
                        "language": resolved.selection.language,
                    },
                },
            ),
        )
        cache_dir = project.root / "synthesis" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        items: list[dict[str, Any]] = []
        cached: dict[str, SynthesisArtifact] = {}
        stale: list[dict[str, Any]] = []
        for segment_index, segment in enumerate(plan.segments):
            segment_id = str(segment.id)
            if segment_id not in unit_selection.segment_ids:
                continue
            unit = units_by_segment[segment_id]
            speech_hash = segment_speech_hash(plan, segment, profile.payload["canonical"])
            key = segment_synthesis_key(speech_hash, profile.profile_id)
            cache_path = cache_dir / f"{_safe_key(key)}.wav"
            sidecar_path = cache_dir / f"{_safe_key(key)}.json"
            item: dict[str, Any] = {
                "segment": segment,
                "segment_id": segment_id,
                "segment_index": segment_index,
                "render_index": segment_index,
                "unit": unit,
                "speech_hash": speech_hash,
                "synthesis_key": key,
                "profile_id": profile.profile_id,
                "cache_path": cache_path,
                "sidecar_path": sidecar_path,
                "path": project.root / "synthesis" / "segments" / f"seg-{segment_index:06d}.wav",
            }
            items.append(item)
            artifact = _artifact_from_item(project, item)
            if artifact is None:
                stale.append(item)
            else:
                cached[segment_id] = artifact
        _emit(
            on_event,
            SynthesisEvent(
                "cache_scanned",
                scope_id="document",
                completed=len(cached),
                total=len(items),
                details={
                    "reused": len(cached),
                    "rendered": len(stale),
                    "required": len(items),
                    "missing": len(stale),
                },
            ),
        )
        render_details = _render_missing(
            project,
            plan,
            adapter,
            resolved.selection,
            stale,
            profile,
            on_event=on_event,
        )
        for item in stale:
            artifact = _artifact_from_item(project, item)
            if artifact is None:
                raise ValueError(f"synthesis did not persist valid audio for {item['segment_id']}")
            cached[item["segment_id"]] = artifact
        if activate:
            _emit(
                on_event,
                SynthesisEvent(
                    "activation_started",
                    scope_id="document",
                    completed=0,
                    total=len(cached),
                ),
            )
            for artifact in cached.values():
                _link_or_copy(artifact.cache_path, artifact.path)
            atomic_write_json(project.paths["synthesis_profile"], profile.to_dict())
            plan_path = project.root / "plan" / "document.utterplan.json"
            segment_rows = []
            for item in items:
                artifact = cached[item["segment_id"]]
                segment_rows.append(
                    {
                        **artifact.to_dict(project.root),
                        "unit_id": item["unit"].id,
                        "unit_index": int(item["unit"].index),
                        **(
                            {"diagnostics": dict(render_details[item["segment_index"]])}
                            if item["segment_index"] in render_details
                            else {}
                        ),
                    }
                )
            compatibility_units = []
            for unit in selected_units:
                unit_items = [item for item in items if item["unit"].id == unit.id]
                if len(unit_items) == 1:
                    compatibility_units.append(
                        {
                            **cached[unit_items[0]["segment_id"]].to_dict(project.root),
                            "unit_id": unit.id,
                            "unit_index": int(unit.index),
                            "content_hash": unit.content_hash,
                        }
                    )
            finished_wall = datetime.now(timezone.utc).isoformat()
            trace = {
                "format": "readio.synthesis-trace",
                "schema_version": 2,
                "started_at": started_wall,
                "finished_at": finished_wall,
                "engine_open_ms": None,
                "render_ms": round((time.monotonic() - started_at) * 1000, 3),
                "selected_units": len(selected_units),
                "selected_segments": len(items),
                "reused_segments": len(items) - len(stale),
                "rendered_segments": len(stale),
                "diagnostics": {"short_sentence_fallbacks": 0, "timing_failures": 0},
                "profile": {"profile_id": profile.profile_id, **dict(profile.payload)},
                "plans": [
                    {
                        "scope_id": "document",
                        "plan_id": plan.plan_id,
                        "plan_sha256": hash_file(plan_path),
                    }
                ],
                "segments": segment_rows,
                "units": compatibility_units,
            }
            atomic_write_json(project.paths["synthesis_trace"], trace)
            _emit(
                on_event,
                SynthesisEvent(
                    "activation_finished",
                    scope_id="document",
                    completed=len(cached),
                    total=len(cached),
                    details={"profile_id": profile.profile_id},
                ),
            )
        _emit(
            on_event,
            SynthesisEvent(
                "complete",
                scope_id="document",
                completed=len(items),
                total=len(items),
                details={
                    "profile_id": profile.profile_id,
                    "reused": len(items) - len(stale),
                    "rendered": len(stale),
                    "activated": activate,
                },
            ),
        )
        return {
            "profile": profile,
            "selection": unit_selection,
            "plan_id": plan.plan_id,
            "scope": "document",
            "reused": len(items) - len(stale),
            "rendered": len(stale),
            "artifacts": tuple(cached.values()),
            "activated": activate,
        }


__all__ = [
    "SynthesisArtifact",
    "SynthesisEvent",
    "SynthesisProfile",
    "segment_speech_hash",
    "segment_synthesis_key",
    "synthesis_profile_id",
    "synthesize_project",
    "unit_synthesis_key",
]
