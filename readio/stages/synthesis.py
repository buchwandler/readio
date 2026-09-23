"""Incremental, content-addressed project synthesis stage."""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import time
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import soundfile as sf

from ..engines.base import EngineSelection
from ..engines.registry import engine_for_ssmd_provider, get_engine
from ..plan import InputRequest, OutputRequest, PlanRequest, SynthesisRequest, resolve_execution_v2
from ..project import Project, atomic_write_json, canonical_json, hash_file, project_lock, read_json
from ..project_settings import (
    project_voice_binding_providers,
    project_voice_bindings,
    project_voice_bindings_provenance,
    project_voice_provider,
    resolve_project_voice_provider,
)
from ..selection import resolve_project_selection, resolve_unit_selection
from ..ssmd import resolve_voice_references
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
    schema_version: int = 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "readio.synthesis-profile",
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            **dict(self.payload),
        }

@dataclass(frozen=True, slots=True)
class ProjectSynthesisRoute:
    provider: str
    engine: str
    mode: str | None
    bindings_by_scope: Mapping[str, Mapping[str, str]]
    selections: Mapping[str, EngineSelection]
    segment_routes: Mapping[tuple[str, str], str]
    default_selection: EngineSelection

@dataclass(frozen=True, slots=True)
class SynthesisArtifact:
    """A validated canonical speech artifact and its active segment view."""

    unit_id: str
    unit_index: int
    content_hash: str
    scope_id: str
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
            "scope_id": self.scope_id,
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
        "options": {key: value for key, value in selection.options.items() if key not in editorial},
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



def _profile_from_route(
    adapter: Any, route: ProjectSynthesisRoute, profile: SynthesisProfile
) -> SynthesisProfile:
    aggregate = route.mode == "target" or (
        route.mode == "runtime" and len(route.selections) > 1
    )
    if not aggregate:
        return profile

    identity_method = getattr(adapter, "canonical_synthesis_identity", None)
    identities = {
        key: (
            dict(identity_method(selection))
            if callable(identity_method)
            else _fallback_canonical_identity(selection, adapter)
        )
        for key, selection in route.selections.items()
    }
    canonical: dict[str, Any] = {
        "engine": route.engine,
        "engine_version": adapter.version(),
        "ssmd_provider": route.provider,
        "routing_mode": route.mode,
    }
    binding_record = profile.payload.get("project_voice_bindings")
    if isinstance(binding_record, Mapping):
        canonical["project_voice_bindings_sha256"] = binding_record.get("sha256")
    if route.mode == "target":
        canonical["targets"] = {key: identities[key] for key in sorted(identities)}
    else:
        canonical["selections"] = {key: identities[key] for key in sorted(identities)}
    canonical["bindings_by_scope"] = {
        scope_id: dict(sorted(bindings.items()))
        for scope_id, bindings in sorted(route.bindings_by_scope.items())
    }
    identity_payload = {
        "schema": "readio.synthesis-profile.v3",
        "canonical": canonical,
    }
    payload = {
        **identity_payload,
        "composition": profile.payload.get("composition", {}),
    }
    if "project_voice_bindings" in profile.payload:
        payload["project_voice_bindings"] = profile.payload["project_voice_bindings"]
    return SynthesisProfile(
        synthesis_profile_id(identity_payload),
        payload,
        schema_version=3,
    )

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
        speed=reader.speed,
        pause_mode=reader.pause_mode,
        unit=reader.unit,
    )
    return PlanRequest(
        operation="render",
        input=InputRequest(
            document=project.load_document_scope(project.document_scopes()[0]),
            selector="all",
            source_kind="file",
        ),
        synthesis=synthesis,
        output=OutputRequest(mode="file", requested_format="wav", force=True),
    )


def _project_request_with_voice_bindings(
    project: Project, cfg: Any, request: PlanRequest
) -> PlanRequest:
    document = project.load_document_scope(project.document_scopes()[0])
    if document.format != "ssmd" and project.manifest.source_format == "ssmd":
        document = replace(document, format="ssmd")

    project_has_provider = project_voice_provider(project.manifest) is not None or bool(
        project_voice_binding_providers(project.manifest)
    )
    requested_engine = request.synthesis.engine
    provider = resolve_project_voice_provider(
        project.manifest, cfg, explicit_engine=requested_engine
    )
    if requested_engine is None and project_has_provider:
        engine = engine_for_ssmd_provider(provider)
    else:
        engine = requested_engine or cfg.reader.engine
        if engine is not None:
            provider = resolve_project_voice_provider(project.manifest, cfg, explicit_engine=engine)

    voice = request.synthesis.voice
    if voice is None and not project_has_provider and requested_engine is None:
        voice = cfg.reader.voice
    synthesis = replace(request.synthesis, engine=engine, voice=voice)

    scope_bindings = _project_scope_voice_bindings(project, cfg, provider, request.voice_bindings)
    return replace(
        request,
        input=replace(request.input, document=document),
        synthesis=synthesis,
        project_voice_bindings=project_voice_bindings(project.manifest, provider),
        scope_voice_bindings=scope_bindings,
    )

def _project_scope_voice_bindings(
    project: Project,
    cfg: Any,
    provider: str,
    invocation_bindings: Mapping[str, str],
) -> dict[str, dict[str, str]]:
    bindings_by_scope: dict[str, dict[str, str]] = {}
    project_bindings = project_voice_bindings(project.manifest, provider)
    for scope in project.document_scopes():
        document = project.load_document_scope(scope)
        if document.format != "ssmd" and project.manifest.source_format == "ssmd":
            document = replace(document, format="ssmd")
        if document.format != "ssmd":
            bindings_by_scope[scope.id] = {}
            continue
        resolved = resolve_voice_references(
            document.text,
            cfg,
            additional_bindings=invocation_bindings,
            project_bindings=project_bindings,
            provider=provider,
        )
        unresolved = next((item for item in resolved if item.voice is None), None)
        if unresolved is not None:
            raise ValueError(
                f"cannot resolve voice reference {unresolved.reference!r} "
                f"in project scope {scope.id!r}"
            )
        bindings_by_scope[scope.id] = {
            item.reference: item.voice for item in resolved if item.voice is not None
        }
    return bindings_by_scope




def _resolve_profile(
    project: Project, cfg: Any, request: PlanRequest
) -> tuple[Any, Any, SynthesisProfile]:
    if not request.scope_voice_bindings:
        request = _project_request_with_voice_bindings(project, cfg, request)
    requested_engine = request.synthesis.engine or cfg.reader.engine
    requested_adapter = None
    if requested_engine is not None:
        try:
            requested_adapter = get_engine(requested_engine)
        except (ImportError, ValueError):
            pass
    if (
        requested_adapter is not None
        and requested_adapter.capabilities().ssmd_voice_binding_mode == "target"
        and request.synthesis.voice is None
    ):
        seed_voice = next(
            (
                voice
                for bindings in request.scope_voice_bindings.values()
                for voice in bindings.values()
            ),
            None,
        )
        if seed_voice is None:
            raise ValueError(
                "target-routed synthesis requires a base voice when selected segments "
                "do not have resolved voice references"
            )
        request = replace(
            request,
            synthesis=replace(request.synthesis, voice=seed_voice),
        )

    resolved = resolve_execution_v2(cfg, request)
    if not resolved.plan.ok or resolved.selection is None:
        diagnostics = "; ".join(item.message for item in resolved.plan.diagnostics)
        raise ValueError(f"cannot resolve synthesis profile: {diagnostics}")
    adapter = get_engine(resolved.selection.engine)
    profile = _profile_from_selection(adapter, resolved.selection)
    provider = adapter.capabilities().ssmd_provider or resolve_project_voice_provider(
        project.manifest, cfg, explicit_engine=resolved.selection.engine
    )
    project_bindings = project_voice_bindings_provenance(provider, request.project_voice_bindings)
    profile = replace(
        profile, payload={**profile.payload, "project_voice_bindings": project_bindings}
    )
    return resolved, adapter, profile

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



def _segment_voice_reference(segment: Any) -> str | None:
    directives = getattr(segment, "directives", None)
    voice = getattr(directives, "voice", None)
    reference = (
        voice.get("reference") if isinstance(voice, Mapping) else getattr(voice, "reference", None)
    )
    return reference if isinstance(reference, str) and reference else None


def _build_project_synthesis_route(
    project: Project,
    cfg: Any,
    request: PlanRequest,
    adapter: Any,
    default_selection: EngineSelection,
    scoped_plans: tuple[tuple[Any, Any], ...],
    selected_by_scope: Mapping[str, Any],
) -> ProjectSynthesisRoute:
    capabilities = adapter.capabilities()
    provider = capabilities.ssmd_provider or resolve_project_voice_provider(
        project.manifest, cfg, explicit_engine=default_selection.engine
    )
    bindings_by_scope = request.scope_voice_bindings
    if capabilities.ssmd_voice_binding_mode == "target":
        segment_routes: dict[tuple[str, str], str] = {}
        target_languages: dict[str, set[str]] = {}
        explicit_base_voice = (
            default_selection.voice if request.synthesis.voice is not None else None
        )
        for scope, plan in scoped_plans:
            scope_id = scope.id
            selected_ids = set(selected_by_scope[scope_id].segment_ids)
            scope_bindings = bindings_by_scope.get(scope_id, {})
            for segment in plan.segments:
                segment_id = str(segment.id)
                if segment_id not in selected_ids:
                    continue
                reference = _segment_voice_reference(segment)
                if reference is not None:
                    target = scope_bindings.get(reference)
                    if target is None:
                        raise ValueError(
                            f"cannot resolve voice reference {reference!r} "
                            f"in project scope {scope_id!r}"
                        )
                else:
                    target = explicit_base_voice
                    if target is None:
                        raise ValueError(
                            f"selected segment {scope_id}:{segment_id} has no voice reference "
                            "and no base voice is available"
                        )
                segment_routes[(scope_id, segment_id)] = target
                language = getattr(segment, "language", None) or default_selection.language
                target_languages.setdefault(target, set()).add(language)

        if not target_languages:
            raise ValueError("selected segments contain no target voices")
        selections: dict[str, EngineSelection] = {}
        options = {
            key: value
            for key, value in default_selection.options.items()
            if key != "ssmd_voice_bindings"
        }
        validator = getattr(adapter, "validate_selection", None)
        metadata_loader = getattr(adapter, "target_metadata", None)
        for target in sorted(target_languages):
            selection = replace(
                default_selection,
                target_id=target,
                voice=target,
                options=options,
            )
            if validator is not None:
                for language in sorted(target_languages[target]):
                    checked = replace(selection, language=language)
                    errors = [
                        item
                        for item in validator(checked)
                        if getattr(item, "severity", "error") == "error"
                    ]
                    if errors:
                        raise ValueError("; ".join(item.message for item in errors))
            if metadata_loader is not None:
                selection = replace(selection, metadata=dict(metadata_loader(selection)))
            selections[target] = selection
        return ProjectSynthesisRoute(
            provider=provider,
            engine=default_selection.engine,
            mode="target",
            bindings_by_scope=bindings_by_scope,
            selections=selections,
            segment_routes=segment_routes,
            default_selection=default_selection,
        )

    selections = {}
    segment_routes = {}
    for scope, plan in scoped_plans:
        scope_id = scope.id
        selected_ids = set(selected_by_scope[scope_id].segment_ids)
        scope_bindings = dict(bindings_by_scope.get(scope_id, {}))
        if capabilities.ssmd_voice_binding_mode == "runtime":
            options = {
                key: value
                for key, value in default_selection.options.items()
                if key != "ssmd_voice_bindings"
            }
            if scope_bindings:
                options["ssmd_voice_bindings"] = scope_bindings
            route_key = "runtime:" + hashlib.sha256(canonical_json(scope_bindings)).hexdigest()
            selections.setdefault(route_key, replace(default_selection, options=options))
        else:
            route_key = "default"
            selections.setdefault(route_key, default_selection)
        for segment in plan.segments:
            segment_id = str(segment.id)
            if segment_id in selected_ids:
                segment_routes[(scope_id, segment_id)] = route_key
    return ProjectSynthesisRoute(
        provider=provider,
        engine=default_selection.engine,
        mode=capabilities.ssmd_voice_binding_mode,
        bindings_by_scope=bindings_by_scope,
        selections=selections,
        segment_routes=segment_routes,
        default_selection=default_selection,
    )

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
    session: Any | None = None,
    scope_id: str = "document",
) -> dict[int, Mapping[str, Any]]:
    if not stale:
        return {}
    details_by_index: dict[int, Mapping[str, Any]] = {}
    total = len(stale)
    if session is None:
        _emit(on_event, SynthesisEvent("engine_open_started", scope_id=scope_id, total=total))
        engine_started = time.monotonic()
        session_context = adapter.open(selection)
    else:
        session_context = nullcontext(session)
    with session_context as active_session:
        if session is None:
            _emit(
                on_event,
                SynthesisEvent(
                    "engine_open_finished",
                    scope_id=scope_id,
                    total=total,
                    details={"elapsed_ms": round((time.monotonic() - engine_started) * 1000, 3)},
                ),
            )
        use_segments = callable(getattr(active_session, "prepare_segments", None))
        prepare_method = (
            active_session.prepare_segments if use_segments else active_session.prepare_plan
        )
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
                            text=_segment_preview(segment)
                            if use_segments
                            else _unit_preview(plan, unit),
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
                                index = int(
                                    metadata.get("segment_index", metadata.get("unit_index", -1))
                                )
                            expected = item["render_index"] if use_segments else int(unit.index)
                            if index != expected:
                                kind = "segment" if use_segments else "plan unit"
                                raise ValueError(
                                    f"engine returned {kind} index {index}; expected {expected}"
                                )
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
                                text=_segment_preview(segment)
                                if use_segments
                                else _unit_preview(plan, unit),
                                details={
                                    **details,
                                    "segment_ids": [item["segment_id"]],
                                    "render_ms": round(
                                        (time.monotonic() - render_started) * 1000, 3
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


def _render_all_missing(
    project: Project,
    adapter: Any,
    route: ProjectSynthesisRoute,
    work: list[dict[str, Any]],
    profile: SynthesisProfile,
    *,
    on_event: Callable[[SynthesisEvent], None] | None = None,
) -> tuple[dict[tuple[str, int], Mapping[str, Any]], float | None]:
    total = sum(len(item["stale"]) for item in work)
    if not total:
        return {}, None

    details: dict[tuple[str, int], Mapping[str, Any]] = {}
    total_open_ms = 0.0
    for route_key, selection in route.selections.items():
        grouped_work = [
            (
                scope_work,
                [item for item in scope_work["stale"] if item["route_key"] == route_key],
            )
            for scope_work in work
        ]
        grouped_work = [(scope_work, items) for scope_work, items in grouped_work if items]
        group_total = sum(len(items) for _, items in grouped_work)
        if not group_total:
            continue

        target_details = (
            {"target_id": selection.target_id, "voice": selection.voice}
            if route.mode == "target"
            else {}
        )
        _emit(
            on_event,
            SynthesisEvent(
                "engine_open_started",
                total=group_total,
                details=target_details,
            ),
        )
        engine_started = time.monotonic()
        with adapter.open(selection) as session:
            elapsed_ms = round((time.monotonic() - engine_started) * 1000, 3)
            total_open_ms += elapsed_ms
            _emit(
                on_event,
                SynthesisEvent(
                    "engine_open_finished",
                    total=group_total,
                    details={"elapsed_ms": elapsed_ms, **target_details},
                ),
            )
            for scope_work, stale in grouped_work:
                scope_id = scope_work["scope"].id
                rendered = _render_missing(
                    project,
                    scope_work["plan"],
                    adapter,
                    selection,
                    stale,
                    profile,
                    on_event=on_event,
                    session=session,
                    scope_id=scope_id,
                )
                details.update({(scope_id, index): value for index, value in rendered.items()})
    return details, total_open_ms

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
        scope_id=item["scope_id"],
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
    """Synthesize selected stale segments across all ordered project scopes."""
    started_at = time.monotonic()
    started_wall = datetime.now(timezone.utc).isoformat()
    with project_lock(project, operation="synth"):
        plan_scopes = project.load_plan_index().scopes
        scoped_plans = tuple((scope, load_scope_plan(project, scope)) for scope in plan_scopes)
        selection = resolve_project_selection(scoped_plans, selector)
        selected_by_scope = {item.scope_id: item for item in selection.scopes}
        request = _project_request_with_voice_bindings(
            project, cfg, _request_for_project(project, cfg, request)
        )
        resolved, adapter, profile = _resolve_profile(project, cfg, request)
        route = _build_project_synthesis_route(
            project,
            cfg,
            request,
            adapter,
            resolved.selection,
            scoped_plans,
            selected_by_scope,
        )
        profile = _profile_from_route(adapter, route, profile)
        cache_dir = project.root / "synthesis" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        work: list[dict[str, Any]] = []
        items: list[dict[str, Any]] = []
        cached: dict[tuple[str, str], SynthesisArtifact] = {}
        stale: list[dict[str, Any]] = []
        selected_units_count = 0
        pending_keys: set[str] = set()

        for scope, plan in scoped_plans:
            scoped_selection = selected_by_scope[scope.id]
            selected_indices = set(scoped_selection.unit_indices)
            selected_units = [unit for unit in plan.units if int(unit.index) in selected_indices]
            selected_units_count += len(selected_units)
            units_by_segment: dict[str, Any] = {}
            for unit in selected_units:
                for segment_id in unit.segment_ids:
                    units_by_segment.setdefault(str(segment_id), unit)
            selected_segment_ids = set(scoped_selection.segment_ids)
            scope_work = {
                "scope": scope,
                "plan": plan,
                "selection": scoped_selection,
                "selected_units": selected_units,
                "items": [],
                "cached": {},
                "stale": [],
            }
            for segment_index, segment in enumerate(plan.segments):
                segment_id = str(segment.id)
                if segment_id not in selected_segment_ids:
                    continue
                route_key = route.segment_routes.get((scope.id, segment_id))
                if route_key is None:
                    raise ValueError(f"no synthesis route for {scope.id}:{segment_id}")
                unit = units_by_segment[segment_id]
                speech_hash = segment_speech_hash(plan, segment, profile.payload["canonical"])
                key = segment_synthesis_key(speech_hash, profile.profile_id)
                item: dict[str, Any] = {
                    "scope_id": scope.id,
                    "route_key": route_key,
                    "segment": segment,
                    "segment_id": segment_id,
                    "segment_index": segment_index,
                    "render_index": segment_index,
                    "unit": unit,
                    "speech_hash": speech_hash,
                    "synthesis_key": key,
                    "profile_id": profile.profile_id,
                    "cache_path": cache_dir / f"{_safe_key(key)}.wav",
                    "sidecar_path": cache_dir / f"{_safe_key(key)}.json",
                    "path": (
                        project.root
                        / "synthesis"
                        / "segments"
                        / (
                            f"seg-{segment_index:06d}.wav"
                            if scope.id == "document"
                            and scope.kind == "document"
                            and len(scoped_plans) == 1
                            else Path(scope.id) / f"seg-{segment_index:06d}.wav"
                        )
                    ),
                }
                items.append(item)
                scope_work["items"].append(item)
                artifact = _artifact_from_item(project, item)
                key_by_scope = (scope.id, segment_id)
                if artifact is None:
                    if key not in pending_keys:
                        pending_keys.add(key)
                        stale.append(item)
                        scope_work["stale"].append(item)
                else:
                    cached[key_by_scope] = artifact
                    scope_work["cached"][segment_id] = artifact
            work.append(scope_work)

        if not items:
            raise ValueError("selected units contain no readable plan segments")
        included_work = [
            item
            for item in work
            if item["items"] or (selection.description == "all" and not item["plan"].units)
        ]
        _emit(
            on_event,
            SynthesisEvent(
                "profile_resolved",
                total=len(items),
                details={
                    "project": str(project.root),
                    "source": str(project.manifest.source_path),
                    "source_format": project.manifest.source_format,
                    "plans": [
                        {"scope_id": scope.id, "plan_id": plan.plan_id}
                        for scope, plan in scoped_plans
                    ],
                    "selected_units": selected_units_count,
                    "selected_segments": len(items),
                    "profile_id": profile.profile_id,
                    "engine": profile.payload.get("canonical", {}).get("engine"),
                    "engine_version": profile.payload.get("canonical", {}).get("engine_version"),
                    "provider": route.provider,
                    "routing_mode": route.mode,
                    "targets": (
                        [
                            {"id": target, "voice": target}
                            for target in sorted(route.selections)
                        ]
                        if route.mode == "target"
                        else []
                    ),
                    "voice_bindings": [
                        {"role": role, "voice": voice}
                        for role, voice in sorted(
                            {
                                (role, voice)
                                for bindings in route.bindings_by_scope.values()
                                for role, voice in bindings.items()
                            }
                        )
                    ],
                    "target": {
                        "id": resolved.selection.target_id,
                        "voice": resolved.selection.voice,
                        "language": resolved.selection.language,
                    },
                },
            ),
        )
        per_scope = {
            scope_work["scope"].id: {
                "required": len(scope_work["items"]),
                "reused": len(scope_work["items"]) - len(scope_work["stale"]),
                "rendered": len(scope_work["stale"]),
            }
            for scope_work in included_work
        }
        _emit(
            on_event,
            SynthesisEvent(
                "cache_scanned",
                completed=len(items) - len(stale),
                total=len(items),
                details={
                    "reused": len(items) - len(stale),
                    "rendered": len(stale),
                    "required": len(items),
                    "missing": len(stale),
                    "scopes": len(included_work),
                    "per_scope": per_scope,
                },
            ),
        )
        render_details, engine_open_ms = _render_all_missing(
            project,
            adapter,
            route,
            work,
            profile,
            on_event=on_event,
        )
        for item in items:
            key_by_scope = (item["scope_id"], item["segment_id"])
            if key_by_scope in cached:
                continue
            artifact = _artifact_from_item(project, item)
            if artifact is None:
                raise ValueError(
                    f"synthesis did not persist valid audio for {item['scope_id']}:{item['segment_id']}"
                )
            cached[key_by_scope] = artifact

        if activate:
            _emit(
                on_event,
                SynthesisEvent(
                    "activation_started",
                    completed=0,
                    total=len(cached),
                    details={"scopes": len(included_work)},
                ),
            )
            for artifact in cached.values():
                _link_or_copy(artifact.cache_path, artifact.path)
            atomic_write_json(project.paths["synthesis_profile"], profile.to_dict())
            segment_rows = []
            compatibility_units = []
            for scope_work in included_work:
                scope_id = scope_work["scope"].id
                plan = scope_work["plan"]
                for item in scope_work["items"]:
                    artifact = cached[(scope_id, item["segment_id"])]
                    segment_rows.append(
                        {
                            **artifact.to_dict(project.root),
                            "unit_id": item["unit"].id,
                            "unit_index": int(item["unit"].index),
                            **(
                                {
                                    "diagnostics": dict(
                                        render_details[(scope_id, item["segment_index"])]
                                    )
                                }
                                if (scope_id, item["segment_index"]) in render_details
                                else {}
                            ),
                        }
                    )
                for unit in scope_work["selected_units"]:
                    unit_items = [
                        item for item in scope_work["items"] if item["unit"].id == unit.id
                    ]
                    if len(unit_items) == 1:
                        item = unit_items[0]
                        compatibility_units.append(
                            {
                                **cached[(scope_id, item["segment_id"])].to_dict(project.root),
                                "unit_id": unit.id,
                                "unit_index": int(unit.index),
                                "content_hash": unit.content_hash,
                            }
                        )

            finished_wall = datetime.now(timezone.utc).isoformat()
            trace = {
                "format": "readio.synthesis-trace",
                "schema_version": 3,
                "started_at": started_wall,
                "finished_at": finished_wall,
                "engine_open_ms": engine_open_ms,
                "render_ms": round((time.monotonic() - started_at) * 1000, 3),
                "selected_units": selected_units_count,
                "selected_segments": len(items),
                "reused_segments": len(cached) - len(stale),
                "rendered_segments": len(stale),
                "diagnostics": {"short_sentence_fallbacks": 0, "timing_failures": 0},
                "profile": {"profile_id": profile.profile_id, **dict(profile.payload)},
                "plans": [
                    {
                        "scope_id": scope_work["scope"].id,
                        "plan_id": scope_work["plan"].plan_id,
                        "plan_sha256": hash_file(project.root / "plan" / scope_work["scope"].path),
                    }
                    for scope_work in included_work
                ],
                "per_scope": per_scope,
                "segments": segment_rows,
                "units": compatibility_units,
            }
            atomic_write_json(project.paths["synthesis_trace"], trace)
            _emit(
                on_event,
                SynthesisEvent(
                    "activation_finished",
                    completed=len(cached),
                    total=len(cached),
                    details={"profile_id": profile.profile_id},
                ),
            )

        _emit(
            on_event,
            SynthesisEvent(
                "complete",
                completed=len(items),
                total=len(items),
                details={
                    "profile_id": profile.profile_id,
                    "reused": len(cached) - len(stale),
                    "rendered": len(stale),
                    "activated": activate,
                },
            ),
        )
        output_selection = (
            resolve_unit_selection(scoped_plans[0][1], selector)
            if len(scoped_plans) == 1
            else selection
        )
        only_scope = included_work[0]["scope"].id if len(included_work) == 1 else None
        only_plan = included_work[0]["plan"].plan_id if len(included_work) == 1 else None
        return {
            "profile": profile,
            "selection": output_selection,
            "plan_id": only_plan,
            "plan_ids": [
                {"scope_id": item["scope"].id, "plan_id": item["plan"].plan_id}
                for item in included_work
            ],
            "scope": only_scope,
            "reused": len(cached) - len(stale),
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
