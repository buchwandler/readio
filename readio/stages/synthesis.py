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
from ..project import Project, atomic_write_json, canonical_json, hash_file, project_lock
from ..selection import resolve_unit_selection
from .planning import load_scope_plan


@dataclass(frozen=True, slots=True)
class SynthesisEvent:
    """Engine-neutral lifecycle event emitted by project synthesis."""

    kind: str
    scope_id: str | None = None
    unit_id: str | None = None
    unit_index: int | None = None
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
            "schema_version": 1,
            "profile_id": self.profile_id,
            **dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class SynthesisArtifact:
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

    def to_dict(self, root: Path) -> dict[str, Any]:
        return {
            "scope_id": "document",
            "unit_id": self.unit_id,
            "unit_index": self.unit_index,
            "content_hash": self.content_hash,
            "synthesis_key": self.synthesis_key,
            "path": self.path.relative_to(root).as_posix(),
            "cache_path": self.cache_path.relative_to(root).as_posix(),
            "audio_sha256": self.audio_sha256,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "frames": self.frames,
            "markers": list(self.markers),
        }


def synthesis_profile_id(payload: Mapping[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(payload)).hexdigest()}"


def unit_synthesis_key(content_hash: str, profile_id: str) -> str:
    payload = {
        "schema": "readio.synthesis-unit.v1",
        "content_hash": content_hash,
        "profile_id": profile_id,
    }
    return f"sha256:{hashlib.sha256(canonical_json(payload)).hexdigest()}"


def _safe_key(key: str) -> str:
    return key.replace(":", "-")


def _profile_from_selection(adapter: Any, selection: Any) -> SynthesisProfile:
    payload: dict[str, Any] = {
        "schema": "readio.synthesis-profile.v1",
        "engine": selection.engine,
        "engine_version": adapter.version(),
        "target": {
            "id": selection.target_id,
            "language": selection.language,
            "voice": selection.voice,
            "speaker": selection.speaker,
            "metadata": dict(selection.metadata),
        },
        "rate": selection.options.get("rate", selection.options.get("speed", 1.0)),
        "options": dict(selection.options),
    }
    return SynthesisProfile(synthesis_profile_id(payload), payload)


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


def _render_missing(
    project: Project,
    plan: Any,
    adapter: Any,
    selection: Any,
    stale: list[Any],
    profile: SynthesisProfile,
    *,
    on_event: Callable[[SynthesisEvent], None] | None = None,
    scope_id: str = "document",
    started_at: float | None = None,
) -> dict[int, Mapping[str, Any]]:
    if not stale:
        return {}
    cache_dir = project.root / "synthesis" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
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
        _emit(on_event, SynthesisEvent("prepare_started", scope_id=scope_id, total=total))
        prepare_started = time.monotonic()
        with session.prepare_plan(plan, options=selection.options) as prepared:
            _emit(
                on_event,
                SynthesisEvent(
                    "prepare_finished",
                    scope_id=scope_id,
                    total=total,
                    details={"elapsed_ms": round((time.monotonic() - prepare_started) * 1000, 3)},
                ),
            )
            by_index = {int(unit.index): unit for unit in plan.units}
            render_indices = tuple(int(unit.index) for unit in stale)
            render_iter = iter(prepared.render(indices=render_indices))
            try:
                for completed, unit in enumerate(stale, 1):
                    expected_index = int(unit.index)
                    _emit(
                        on_event,
                        SynthesisEvent(
                            "unit_started",
                            scope_id=scope_id,
                            unit_id=unit.id,
                            unit_index=expected_index,
                            completed=completed - 1,
                            total=total,
                            text=_unit_preview(plan, unit),
                            details={"segment_ids": list(unit.segment_ids)},
                        ),
                    )
                    render_started = time.monotonic()
                    try:
                        result = next(render_iter)
                    except StopIteration as exc:
                        raise ValueError(
                            f"engine stopped rendering before plan unit {unit.id} "
                            f"(index {expected_index})"
                        ) from exc
                    try:
                        descriptor = getattr(result, "descriptor", None)
                        index = int(
                            getattr(
                                result,
                                "index",
                                getattr(
                                    result,
                                    "unit_index",
                                    getattr(descriptor, "index", -1),
                                ),
                            )
                        )
                        if index < 0:
                            metadata = getattr(result, "metadata", {}) or {}
                            index = int(metadata.get("unit_index", metadata.get("index", -1)))
                        if index != expected_index:
                            raise ValueError(
                                "engine returned plan unit index "
                                f"{index}; expected {expected_index}"
                            )
                        rendered_unit = by_index[index]
                        key = unit_synthesis_key(rendered_unit.content_hash, profile.profile_id)
                        cache_path = cache_dir / f"{_safe_key(key)}.wav"
                        temporary = cache_path.with_name(f".tmp-{secrets.token_hex(8)}.wav")
                        try:
                            sf.write(
                                temporary,
                                result.audio,
                                int(result.sample_rate),
                                subtype="PCM_16",
                            )
                            checked = _valid_audio(temporary)
                            if checked is None:
                                raise ValueError(
                                    f"engine produced invalid audio for {rendered_unit.id}"
                                )
                            os.replace(temporary, cache_path)
                        finally:
                            temporary.unlink(missing_ok=True)
                        result_details = _result_details(result)
                        details_by_index[index] = result_details
                        _emit(
                            on_event,
                            SynthesisEvent(
                                "unit_finished",
                                scope_id=scope_id,
                                unit_id=rendered_unit.id,
                                unit_index=index,
                                completed=completed,
                                total=total,
                                text=_unit_preview(plan, rendered_unit),
                                details={
                                    **result_details,
                                    "segment_ids": list(rendered_unit.segment_ids),
                                    "render_ms": round(
                                        (time.monotonic() - render_started) * 1000,
                                        3,
                                    ),
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


def synthesize_project(
    project: Project,
    cfg: Any,
    *,
    request: PlanRequest | None = None,
    selector: str = "all",
    activate: bool = True,
    on_event: Callable[[SynthesisEvent], None] | None = None,
) -> dict[str, Any]:
    """Synthesize selected stale units, loading the engine only when needed."""
    started_at = time.monotonic()
    started_wall = datetime.now(timezone.utc).isoformat()
    with project_lock(project, operation="synth"):
        plan = load_scope_plan(project)
        unit_selection = resolve_unit_selection(plan, selector)
        request = _request_for_project(project, cfg, request)
        resolved, adapter, profile = _resolve_profile(project, cfg, request)
        _emit(
            on_event,
            SynthesisEvent(
                "profile_resolved",
                scope_id="document",
                total=len(unit_selection.unit_indices),
                details={
                    "project": str(project.root),
                    "source": str(project.manifest.source_path),
                    "source_format": project.manifest.source_format,
                    "plan_id": plan.plan_id,
                    "selected_units": len(unit_selection.unit_indices),
                    "profile_id": profile.profile_id,
                    "engine": profile.payload.get("engine"),
                    "engine_version": profile.payload.get("engine_version"),
                    "target": dict(profile.payload.get("target", {})),
                    "rate": profile.payload.get("rate"),
                },
            ),
        )
        selected_indices = set(unit_selection.unit_indices)
        selected = [unit for unit in plan.units if int(unit.index) in selected_indices]
        cache_dir = project.root / "synthesis" / "cache"
        stale: list[Any] = []
        cached: dict[int, SynthesisArtifact] = {}
        for unit in selected:
            key = unit_synthesis_key(unit.content_hash, profile.profile_id)
            cache_path = cache_dir / f"{_safe_key(key)}.wav"
            checked = _valid_audio(cache_path)
            if checked is None:
                stale.append(unit)
                continue
            rate, channels, frames, digest = checked
            cached[int(unit.index)] = SynthesisArtifact(
                unit.id,
                int(unit.index),
                unit.content_hash,
                key,
                project.root / "synthesis" / "segments" / f"seg-{int(unit.index) + 1:06d}.wav",
                cache_path,
                digest,
                rate,
                channels,
                frames,
            )
        _emit(
            on_event,
            SynthesisEvent(
                "cache_scanned",
                scope_id="document",
                completed=len(cached),
                total=len(selected),
                details={"reused": len(cached), "rendered": len(stale)},
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
        for unit in stale:
            key = unit_synthesis_key(unit.content_hash, profile.profile_id)
            cache_path = cache_dir / f"{_safe_key(key)}.wav"
            checked = _valid_audio(cache_path)
            if checked is None:
                raise ValueError(f"synthesis did not persist valid audio for {unit.id}")
            rate, channels, frames, digest = checked
            cached[int(unit.index)] = SynthesisArtifact(
                unit.id,
                int(unit.index),
                unit.content_hash,
                key,
                project.root / "synthesis" / "segments" / f"seg-{int(unit.index) + 1:06d}.wav",
                cache_path,
                digest,
                rate,
                channels,
                frames,
            )
        finished_wall = datetime.now(timezone.utc).isoformat()
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
            trace = {
                "format": "readio.synthesis-trace",
                "schema_version": 1,
                "started_at": started_wall,
                "finished_at": finished_wall,
                "engine_open_ms": None,
                "render_ms": round((time.monotonic() - started_at) * 1000, 3),
                "selected_units": len(selected),
                "reused_units": len(selected) - len(stale),
                "rendered_units": len(stale),
                "diagnostics": {"short_sentence_fallbacks": 0, "timing_failures": 0},
                "profile": {"profile_id": profile.profile_id, **dict(profile.payload)},
                "plans": [
                    {
                        "scope_id": "document",
                        "plan_id": plan.plan_id,
                        "plan_sha256": hash_file(plan_path),
                    }
                ],
                "units": [
                    {
                        **artifact.to_dict(project.root),
                        **(
                            {"diagnostics": dict(render_details.get(artifact.unit_index, {}))}
                            if artifact.unit_index in render_details
                            else {}
                        ),
                    }
                    for artifact in sorted(cached.values(), key=lambda item: item.unit_index)
                ],
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
                completed=len(selected),
                total=len(selected),
                details={
                    "profile_id": profile.profile_id,
                    "reused": len(selected) - len(stale),
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
            "reused": len(selected) - len(stale),
            "rendered": len(stale),
            "artifacts": tuple(cached.values()),
            "activated": activate,
        }


__all__ = [
    "SynthesisArtifact",
    "SynthesisEvent",
    "SynthesisProfile",
    "synthesis_profile_id",
    "synthesize_project",
    "unit_synthesis_key",
]
