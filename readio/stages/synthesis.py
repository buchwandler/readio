"""Incremental, content-addressed project synthesis stage."""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..config import normalize_language_key
from ..errors import EngineSynthesisError
from ..integrations.voicerender import render_plan as voicerender_render_plan
from ..integrations.voicerender import resolve_profile as resolve_voicerender_profile
from ..plan import (
    InputRequest,
    OutputRequest,
    PlanDiagnostic,
    PlanningPlanV2,
    PlanRequest,
    ReadioPlanV2,
    SynthesisRequest,
)
from ..project import Project, atomic_write_json, canonical_json, hash_file, project_lock
from ..project_settings import (
    effective_project_role_targets,
    merge_project_synthesis_request,
    project_role_targets_provenance,
    project_settings_from_manifest,
    project_synthesis_request,
    project_voice_namespace,
    synthesis_request_fingerprint,
)
from ..role_targets import VoiceTarget, engine_for_ssmd_namespace, ssmd_namespace_for_engine
from ..selection import flow_id, flow_segment_id, resolve_project_selection, resolve_unit_selection
from ..ssmd import resolve_voice_references
from .planning import load_scope_plan


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
    schema_version: int = 3

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "readio.synthesis-profile",
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            **dict(self.payload),
        }


@dataclass(slots=True)
class RenderProgressState:
    """Mutable operation-global counters shared across route-grouped scopes."""

    completed: int = 0
    total: int = 0
    scope_completed: dict[str, int] = field(default_factory=dict)
    scope_totals: dict[str, int] = field(default_factory=dict)


def _synthesis_progress_details(
    progress: RenderProgressState,
    *,
    scope_id: str,
    scope_metadata: Mapping[str, Any],
    scope_completed: int,
) -> dict[str, Any]:
    return {
        **{key: value for key, value in scope_metadata.items() if value is not None},
        "scope_completed": scope_completed,
        "scope_render_total": progress.scope_totals.get(scope_id, 0),
        "global_completed": progress.completed,
        "global_total": progress.total,
    }


@dataclass(frozen=True, slots=True)
class SynthesisArtifact:
    """A speech artifact referenced from a VoiceRender render manifest."""

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
    word_timings: tuple[Mapping[str, Any], ...] = ()
    speech_hash: str | None = None
    segment_id_value: str | None = None
    segment_index_value: int | None = None
    text: str | None = None
    profile_id: str | None = None
    manifest_path: Path | None = None
    manifest_id: str | None = None
    source_plan_id: str | None = None
    source_locator: str | None = None
    acoustic_cache_key: str | None = None

    @property
    def segment_id(self) -> str:
        return self.segment_id_value or self.unit_id

    @property
    def segment_index(self) -> int:
        return self.segment_index_value if self.segment_index_value is not None else self.unit_index

    @property
    def lowering_sha256(self) -> None:
        """No separate Readio lowering artifact exists for VoiceRender requests."""
        return None

    def to_dict(self, root: Path) -> dict[str, Any]:
        data: dict[str, Any] = {
            "scope_id": self.scope_id,
            "segment_id": self.segment_id,
            "segment_index": self.segment_index,
            "speech_hash": self.speech_hash or self.content_hash,
            "synthesis_key": self.synthesis_key,
            "path": self.path.relative_to(root).as_posix(),
            "cache_path": self.cache_path.relative_to(root).as_posix(),
            "audio_sha256": self.audio_sha256,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "frames": self.frames,
            "markers": [dict(item) for item in self.markers],
            "word_timings": [dict(item) for item in self.word_timings],
        }
        if self.text is not None:
            data["text"] = self.text
        if self.manifest_path is not None:
            data["manifest_path"] = self.manifest_path.relative_to(root).as_posix()
        for key, value in (
            ("manifest_id", self.manifest_id),
            ("source_plan_id", self.source_plan_id),
            ("source_locator", self.source_locator),
            ("acoustic_cache_key", self.acoustic_cache_key),
        ):
            if value is not None:
                data[key] = value
        return data


def synthesis_profile_id(payload: Mapping[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(payload)).hexdigest()}"


def _record_project_settings(
    profile: SynthesisProfile, project: Project, request: SynthesisRequest
) -> SynthesisProfile:
    settings = project_settings_from_manifest(project.manifest, project.state_root).synthesis
    if settings is None:
        return profile
    fingerprint = synthesis_request_fingerprint(request, project.state_root)
    provenance = {"synthesis_sha256": fingerprint}
    identity = {
        "schema": "readio.project-synthesis-profile.v1",
        "engine_profile_id": profile.profile_id,
        "project_settings": provenance,
    }
    return replace(
        profile,
        profile_id=synthesis_profile_id(identity),
        payload={**profile.payload, "project_settings": provenance},
    )


def _request_for_project(project: Project, cfg: Any, request: PlanRequest | None) -> PlanRequest:
    if request is not None:
        return request
    reader = cfg.reader
    synthesis = SynthesisRequest(
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
    project_targets, _ = effective_project_role_targets(project.manifest)
    active_namespace = project_voice_namespace(project.manifest)
    requested_engine = request.synthesis.engine
    profile_engine = requested_engine or (
        engine_for_ssmd_namespace(active_namespace) if active_namespace is not None else None
    )
    attached_workspace = project.manifest.schema_version == 4
    if profile_engine is None and not attached_workspace:
        profile_engine = cfg.reader.engine
    engine = profile_engine or cfg.reader.engine

    namespace = ssmd_namespace_for_engine(engine)
    voice = request.synthesis.voice
    if (
        voice is None
        and not attached_workspace
        and not project_targets
        and requested_engine is None
    ):
        voice = cfg.reader.voice
    synthesis = replace(request.synthesis, engine=profile_engine, voice=voice)
    scope_targets = _project_scope_voice_targets(
        project,
        cfg,
        namespace,
        request.voice_bindings,
        project_targets,
        engine=engine,
    )
    return replace(
        request,
        input=replace(request.input, document=document),
        synthesis=synthesis,
        project_voice_targets=project_targets,
        scope_voice_targets=scope_targets,
    )


def _project_scope_voice_targets(
    project: Project,
    cfg: Any,
    namespace: str,
    invocation_bindings: Mapping[str, str | VoiceTarget],
    project_targets: Mapping[str, VoiceTarget],
    *,
    engine: str | None = None,
) -> dict[str, dict[str, VoiceTarget]]:
    targets_by_scope: dict[str, dict[str, VoiceTarget]] = {}
    for scope in project.document_scopes():
        document = project.load_document_scope(scope)
        if document.format != "ssmd":
            targets_by_scope[scope.id] = {}
            continue
        resolved = resolve_voice_references(
            document.text,
            cfg,
            additional_bindings=invocation_bindings,
            project_targets=project_targets,
            configured_targets=cfg.roles,
            provider=namespace,
            engine=engine,
            source_path=project.workspace_path(scope.path)
            if project.manifest.schema_version == 4
            else project.path(scope.path),
        )
        unresolved = next((item for item in resolved if item.target is None), None)
        if unresolved is not None:
            message = (
                unresolved.diagnostic.message
                if unresolved.diagnostic is not None
                else f"cannot resolve voice reference {unresolved.reference!r}"
            )
            raise ValueError(f"{message} in project scope {scope.id!r}")
        targets_by_scope[scope.id] = {
            item.reference: item.target for item in resolved if item.target is not None
        }
    return targets_by_scope


def _language_defaults(cfg: Any, language: str) -> Any | None:
    requested = normalize_language_key(language)
    for key, settings in getattr(cfg, "languages", {}).items():
        try:
            candidate = normalize_language_key(str(key))
        except (TypeError, ValueError):
            continue
        if candidate == requested or candidate.split("-", 1)[0] == requested.split("-", 1)[0]:
            return settings
    return None


def _voice_binding_request(
    cfg: Any,
    request: PlanRequest,
    language: str,
    target: VoiceTarget | None = None,
) -> dict[str, Any]:
    synthesis = request.synthesis
    language_defaults = _language_defaults(cfg, language)
    reader = cfg.reader
    default_engine = synthesis.engine or getattr(language_defaults, "engine", None) or reader.engine
    engine = target.engine if target is not None else default_engine
    target_id = target.target_id if target is not None else None
    if target_id is None and engine == default_engine:
        target_id = synthesis.model or getattr(language_defaults, "model", None)
    voice = (
        target.voice
        if target is not None
        else (synthesis.voice or getattr(language_defaults, "voice", None) or reader.voice)
    )
    options = dict(synthesis.engine_options)
    speed = synthesis.speed if synthesis.speed is not None else reader.speed
    if speed is not None:
        options["speed"] = speed
    voice_level = synthesis.voice_level or reader.voice_level
    if voice_level and voice_level != "off":
        options["voice_level"] = voice_level
    for key in (
        "quality",
        "model_source",
        "lexicons",
        "g2p_fallback",
        "lexicon_data_policy",
        "language_detection",
        "detect_languages",
        "allow_experimental",
        "short_sentence",
    ):
        value = getattr(synthesis, key, None)
        if key == "model_source" and value is None:
            value = getattr(language_defaults, "source", None)
        if value is None:
            value = getattr(language_defaults, key, None)
        if value is not None:
            options[key] = value
    if synthesis.voice_prompt is not None:
        options["voice_source"] = {"kind": "prompt", "value": synthesis.voice_prompt}
    return {
        "engine": engine,
        "target_id": target_id,
        "language": language,
        "voice": voice,
        "speaker": synthesis.speaker,
        "options": options,
        "engine_options": dict(synthesis.engine_options),
        "offline": synthesis.offline,
        "refresh": synthesis.refresh,
        "reference_audio": synthesis.voice_file,
    }


def resolve_project_synthesis(
    project: Project,
    cfg: Any,
    request: PlanRequest | None = None,
    *,
    merge_saved_settings: bool = True,
) -> tuple[PlanRequest, Any, Any, SynthesisProfile]:
    """Resolve default project voice settings through VoiceRender's public API."""
    saved = (
        project_settings_from_manifest(project.manifest, project.state_root).synthesis
        if merge_saved_settings
        else None
    )
    effective_request = _request_for_project(project, cfg, request)
    if saved is not None:
        if request is None:
            effective_request = replace(effective_request, synthesis=SynthesisRequest())
        effective_request = replace(
            effective_request,
            synthesis=merge_project_synthesis_request(saved, effective_request.synthesis),
        )
    effective_request = _project_request_with_voice_bindings(project, cfg, effective_request)
    language = effective_request.synthesis.language
    if language is None:
        try:
            plan_scopes = project.load_plan_index().scopes
            language = next(
                (
                    segment.language
                    for scope in plan_scopes
                    for flow in load_scope_plan(project, scope).flow
                    for segment in flow.segments
                ),
                cfg.reader.lang,
            )
        except (OSError, KeyError, ValueError):
            language = cfg.reader.lang
    first_scope_targets = next(iter(effective_request.scope_voice_targets.values()), {})
    binding_target = next(iter(first_scope_targets.values()), None)
    binding = _voice_binding_request(cfg, effective_request, language, binding_target)
    resolved_profile = resolve_voicerender_profile(
        {"narrator": binding},
        name=f"Readio project {project.manifest.name}",
        default_role="narrator",
        language=language,
    )
    selection = resolved_profile.selections["narrator"]
    identity = {
        "engine": selection.engine,
        "target_id": selection.target_id,
        "voice": selection.voice,
        "language": selection.language,
        "options": dict(selection.options),
    }
    profile = SynthesisProfile(
        synthesis_profile_id({"schema": "voicerender.selection.v1", **identity}),
        {
            "schema": "readio.synthesis-profile.v6",
            "canonical": identity,
            "composition": {},
        },
        schema_version=6,
    )
    planning = PlanningPlanV2(
        language=language,
        unit=effective_request.synthesis.unit or cfg.reader.unit,
        pause_mode=effective_request.synthesis.pause_mode or cfg.reader.pause_mode,
        spacy=effective_request.synthesis.spacy or cfg.reader.spacy,
        language_detection=(
            effective_request.synthesis.language_detection or cfg.reader.language_detection
        ),
        detect_languages=(
            effective_request.synthesis.detect_languages or cfg.reader.detect_languages
        ),
    )
    diagnostics = tuple(
        PlanDiagnostic(
            code=item.code,
            severity=item.severity,
            message=item.message,
            field=getattr(item, "field", None),
        )
        for item in resolved_profile.diagnostics
    )
    resolved_plan = ReadioPlanV2(
        operation=effective_request.operation,
        planning=planning,
        diagnostics=diagnostics,
    )
    return (
        effective_request,
        SimpleNamespace(selection=selection, plan=resolved_plan),
        None,
        profile,
    )


def _emit(on_event: Callable[[SynthesisEvent], None] | None, event: SynthesisEvent) -> None:
    if on_event is not None:
        on_event(event)


def _segment_voice_reference(segment: Any) -> str | None:
    directives = getattr(segment, "directives", None)
    voice = getattr(directives, "voice", None)
    reference = (
        voice.get("reference") if isinstance(voice, Mapping) else getattr(voice, "reference", None)
    )
    return reference if isinstance(reference, str) and reference else None


def synthesize_project(
    project: Project,
    cfg: Any,
    *,
    request: PlanRequest | None = None,
    selector: str = "all",
    activate: bool = True,
    on_event: Callable[[SynthesisEvent], None] | None = None,
) -> dict[str, Any]:
    """Render selected project flows through VoiceRender's public v5 contract."""
    started_at = time.monotonic()
    started_wall = datetime.now(timezone.utc).isoformat()
    with project_lock(project, operation="synth"):
        plan_scopes = project.load_plan_index().scopes
        scoped_plans = tuple((scope, load_scope_plan(project, scope)) for scope in plan_scopes)
        document_scopes = {scope.id: scope for scope in project.document_scopes()}
        selection = resolve_project_selection(scoped_plans, selector)
        selected_by_scope = {item.scope_id: item for item in selection.scopes}
        selected_scope_ids = [item.scope_id for item in selection.scopes if item.segment_ids]
        scope_metadata_by_id: dict[str, dict[str, Any]] = {}
        for scope_index, scope_id in enumerate(selected_scope_ids, 1):
            document_scope = document_scopes.get(scope_id)
            if document_scope is not None:
                scope_metadata_by_id[scope_id] = {
                    "scope_kind": document_scope.kind,
                    "scope_title": document_scope.title,
                    "scope_number": document_scope.source_number,
                    "scope_index": scope_index,
                    "scope_total": len(selected_scope_ids),
                }

        saved_synthesis = project_settings_from_manifest(
            project.manifest, project.state_root
        ).synthesis
        requested_synthesis = (
            request.synthesis
            if request is not None
            else project_synthesis_request(saved_synthesis)
            if saved_synthesis is not None
            else None
        )
        request, resolved, _adapter, profile = resolve_project_synthesis(project, cfg, request)

        items: list[dict[str, Any]] = []
        selected_units_count = sum(len(item.unit_indices) for item in selection.scopes)
        work_by_scope: dict[str, dict[str, Any]] = {}
        for scope, plan in scoped_plans:
            selected = selected_by_scope[scope.id]
            selected_ids = set(selected.segment_ids)
            scope_items: list[dict[str, Any]] = []
            for flow_index, flow in enumerate(plan.flow):
                current_flow_id = flow_id(flow_index, flow)
                for segment_index, segment in enumerate(flow.segments):
                    segment_id = flow_segment_id(flow_index, flow, segment_index)
                    if segment_id not in selected_ids:
                        continue
                    item = {
                        "scope": scope,
                        "plan": plan,
                        "scope_id": scope.id,
                        "scope_metadata": scope_metadata_by_id.get(scope.id, {}),
                        "flow": flow,
                        "flow_index": flow_index,
                        "flow_id": current_flow_id,
                        "flow_hash": flow.content_hash,
                        "segment": segment,
                        "segment_id": segment_id,
                        "segment_index": segment_index,
                        "source_order": sum(
                            len(previous_flow.segments) for previous_flow in plan.flow[:flow_index]
                        )
                        + segment_index,
                        "locator": f"f{flow_index:06d}.s{segment_index:03d}",
                        "language": segment.language,
                    }
                    scope_items.append(item)
                    items.append(item)
            work_by_scope[scope.id] = {
                "scope": scope,
                "plan": plan,
                "items": scope_items,
            }
        if not items:
            raise ValueError("selected flows contain no readable plan segments")

        _emit(
            on_event,
            SynthesisEvent(
                "profile_resolved",
                total=len(items),
                details={
                    "project": str(project.root),
                    "plans": [
                        {"scope_id": scope.id, "plan_id": plan.plan_id}
                        for scope, plan in scoped_plans
                    ],
                    "selected_units": selected_units_count,
                    "selected_segments": len(items),
                    "profile_id": profile.profile_id,
                    "engine": resolved.selection.engine,
                    "engines": sorted(
                        {resolved.selection.engine}
                        | {
                            target.engine
                            for bindings in request.scope_voice_targets.values()
                            for target in bindings.values()
                        }
                    ),
                    "routing_mode": "voice-render-profile",
                    "voice_bindings": [
                        {"scope_id": scope_id, "role": role, **target.to_dict()}
                        for scope_id, bindings in sorted(request.scope_voice_targets.items())
                        for role, target in sorted(bindings.items())
                    ],
                },
            ),
        )

        cache_dir = project.state_root / "synthesis" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        progress = RenderProgressState(
            total=len(items),
            scope_totals={scope_id: len(work["items"]) for scope_id, work in work_by_scope.items()},
        )
        artifacts_by_key: dict[tuple[str, str], SynthesisArtifact] = {}
        manifest_refs: list[dict[str, Any]] = []
        render_counts = {"rendered": 0, "reused": 0}
        counts_by_scope = {scope_id: {"rendered": 0, "reused": 0} for scope_id in work_by_scope}

        def voice_binding(item: Mapping[str, Any], language: str) -> dict[str, Any]:
            reference = _segment_voice_reference(item["segment"])
            target = (
                request.scope_voice_targets.get(item["scope_id"], {}).get(reference)
                if reference is not None
                else None
            )
            return _voice_binding_request(cfg, request, language, target)

        for (scope_id, language, run_index), group_items_iter in _group_items(items).items():
            group_items = list(group_items_iter)
            scope_work = work_by_scope[scope_id]
            plan = scope_work["plan"]
            role_bindings: dict[str, dict[str, Any]] = {}
            for item in group_items:
                reference = _segment_voice_reference(item["segment"])
                role = reference or "narrator"
                binding = voice_binding(item, language)
                prior = role_bindings.get(role)
                if prior is not None and prior != binding:
                    raise ValueError(
                        f"logical voice {role!r} resolves to multiple VoiceRender targets "
                        f"in project scope {scope_id!r}"
                    )
                role_bindings[role] = binding
            default_role = "narrator" if "narrator" in role_bindings else next(iter(role_bindings))
            resolved_profile = resolve_voicerender_profile(
                role_bindings,
                name=f"Readio project {project.manifest.name}",
                default_role=default_role,
                language=language,
            )
            items_by_locator = {item["locator"]: item for item in group_items}

            group_progress = {"completed": 0}
            group_counts = {"rendered": 0, "reused": 0}
            scope_completed_before = progress.scope_completed.get(scope_id, 0)
            on_render_event = _make_voice_render_event_handler(
                scope_id=scope_id,
                language=language,
                items_by_locator=items_by_locator,
                progress=progress,
                scope_completed_before=scope_completed_before,
                group_progress=group_progress,
                render_counts=render_counts,
                group_counts=group_counts,
                counts_by_scope=counts_by_scope,
                on_event=on_event,
            )
            scope_hash = hashlib.sha256(scope_id.encode("utf-8")).hexdigest()[:12]
            language_key = re.sub(r"[^a-z0-9-]+", "-", language.lower()).strip("-") or "und"
            output_dir = (
                project.state_root
                / "synthesis"
                / "renders"
                / f"scope-{scope_hash}"
                / language_key
                / f"run-{run_index:06d}"
            )
            source_plan_path = project.state_root / "plan" / scope_work["scope"].path
            try:
                bundle = voicerender_render_plan(
                    source_plan_path,
                    profile=resolved_profile,
                    output_dir=output_dir,
                    cache_dir=cache_dir,
                    locators=[item["locator"] for item in group_items],
                    on_event=on_render_event,
                )
            except Exception as error:
                if type(error).__module__.startswith("voicerender"):
                    error_code = getattr(error, "code", "synthesis.voicerender_error")
                    details = {
                        "scope_id": scope_id,
                        "language": language,
                        "voicerender_error": type(error).__name__,
                        "voicerender_code": error_code,
                        "voicerender_details": dict(getattr(error, "details", {}) or {}),
                    }
                    raise EngineSynthesisError(
                        f"VoiceRender failed in project scope {scope_id!r}: {error}",
                        engine=getattr(error, "engine", None),
                        target_id=getattr(error, "target_id", None),
                        details=details,
                        code=str(error_code),
                    ) from error
                raise

            for status in ("rendered", "reused"):
                missing_count = getattr(bundle.result, f"{status}_count") - group_counts[status]
                if missing_count > 0:
                    group_counts[status] += missing_count
                    render_counts[status] += missing_count
                    counts_by_scope[scope_id][status] += missing_count
                    group_progress["completed"] += missing_count
                    progress.completed += missing_count
                    progress.scope_completed[scope_id] = (
                        scope_completed_before + group_progress["completed"]
                    )
            manifest_path = bundle.result.manifest_path
            manifest = bundle.manifest
            manifest_refs.append(
                {
                    "scope_id": scope_id,
                    "language": language,
                    "run_index": run_index,
                    "path": manifest_path.relative_to(project.state_root).as_posix(),
                    "manifest_id": manifest["manifest_id"],
                    "plan_id": manifest["source"]["plan_id"],
                    "plan_file_sha256": manifest["source"]["plan_file_sha256"],
                    "effective_profile_sha256": manifest["profile"]["effective_profile_sha256"],
                }
            )
            entries_by_locator = {
                entry["source"]["locator"]: entry for entry in manifest["segments"]
            }
            for item in group_items:
                item_role = _segment_voice_reference(item["segment"]) or default_role
                entry = entries_by_locator.get(item["locator"])
                if entry is None or entry.get("status") not in {"rendered", "reused"}:
                    raise EngineSynthesisError(
                        f"VoiceRender did not produce {scope_id}:{item['segment_id']}",
                        engine=resolved_profile.selections[item_role].engine,
                        request_id=item["segment_id"],
                        details={
                            "manifest_path": str(manifest_path),
                            "source_locator": item["locator"],
                        },
                    )
                audio = entry["audio"]
                audio_path = manifest_path.parent / audio["path"]
                source = entry["source"]
                profile_id = manifest["profile"]["effective_profile_sha256"]
                marker_values = tuple({"marker": marker} for marker in item["segment"].markers)
                artifact = SynthesisArtifact(
                    unit_id=item["flow_id"],
                    unit_index=item["flow_index"],
                    content_hash=source["semantic_segment_hash"],
                    scope_id=scope_id,
                    synthesis_key=entry["acoustic_cache_key"],
                    path=audio_path,
                    cache_path=audio_path,
                    audio_sha256=audio["sha256"],
                    sample_rate=audio["sample_rate"],
                    channels=audio["channels"],
                    frames=audio["frames"],
                    markers=marker_values,
                    word_timings=tuple(entry["timing"]["spans"]),
                    speech_hash=entry["synthesis_semantics_sha256"],
                    segment_id_value=item["segment_id"],
                    segment_index_value=item["segment_index"],
                    text=item["segment"].text,
                    profile_id=profile_id,
                    manifest_path=manifest_path,
                    manifest_id=manifest["manifest_id"],
                    source_plan_id=manifest["source"]["plan_id"],
                    source_locator=item["locator"],
                    acoustic_cache_key=entry["acoustic_cache_key"],
                )
                artifacts_by_key[(scope_id, item["segment_id"])] = artifact

        _emit(
            on_event,
            SynthesisEvent(
                "cache_scanned",
                completed=render_counts["reused"],
                total=len(items),
                details={
                    "reused": render_counts["reused"],
                    "rendered": render_counts["rendered"],
                    "required": len(items),
                    "manifests": len(manifest_refs),
                },
            ),
        )
        artifacts = tuple(
            artifacts_by_key[(item["scope_id"], item["segment_id"])] for item in items
        )
        identities = sorted({str(item["effective_profile_sha256"]) for item in manifest_refs})
        canonical_profiles: dict[str, Any] = {
            "schema": "voicerender.effective-profile-set.v1",
            "profiles": identities,
        }
        profile_id = (
            identities[0]
            if len(identities) == 1
            else "sha256:" + hashlib.sha256(canonical_json(canonical_profiles)).hexdigest()
        )
        profile = SynthesisProfile(
            profile_id=profile_id,
            schema_version=6,
            payload={
                "schema": "readio.synthesis-profile.v6",
                "canonical": canonical_profiles,
                "composition": dict(profile.payload.get("composition", {})),
                "manifest_refs": manifest_refs,
                "project_role_targets": project_role_targets_provenance(project.manifest),
            },
        )
        if requested_synthesis is not None:
            profile = _record_project_settings(profile, project, requested_synthesis)

        included_work = [work for work in work_by_scope.values() if work["items"]]
        per_scope = {
            work["scope"].id: {
                "required": len(work["items"]),
                **counts_by_scope[work["scope"].id],
            }
            for work in included_work
        }
        if activate:
            atomic_write_json(project.paths["synthesis_profile"], profile.to_dict())
            segment_rows = []
            compatibility_units = []
            for work in included_work:
                scope_id = work["scope"].id
                for item in work["items"]:
                    artifact = artifacts_by_key[(scope_id, item["segment_id"])]
                    segment_rows.append(
                        {
                            **artifact.to_dict(project.state_root),
                            "unit_id": item["flow_id"],
                            "unit_index": item["flow_index"],
                        }
                    )
                for flow_index in sorted({item["flow_index"] for item in work["items"]}):
                    flow = work["plan"].flow[flow_index]
                    flow_segments = [
                        item for item in work["items"] if item["flow_index"] == flow_index
                    ]
                    if len(flow_segments) == 1:
                        item = flow_segments[0]
                        artifact = artifacts_by_key[(scope_id, item["segment_id"])]
                        compatibility_units.append(
                            {
                                **artifact.to_dict(project.state_root),
                                "unit_id": flow_id(flow_index, flow),
                                "unit_index": flow_index,
                                "content_hash": flow.content_hash,
                            }
                        )
            plan_refs = [
                {
                    "scope_id": work["scope"].id,
                    "plan_id": work["plan"].plan_id,
                    "path": work["scope"].path,
                    "sha256": hash_file(project.state_root / "plan" / work["scope"].path),
                }
                for work in included_work
            ]
            trace = {
                "format": "readio.synthesis-trace",
                "schema_version": 4,
                "started_at": started_wall,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "render_ms": round((time.monotonic() - started_at) * 1000, 3),
                "selected_units": selected_units_count,
                "selected_segments": len(items),
                "reused_segments": render_counts["reused"],
                "rendered_segments": render_counts["rendered"],
                "diagnostics": {"voicerender": []},
                "profile": {"profile_id": profile.profile_id, **dict(profile.payload)},
                "plans": plan_refs,
                "manifests": manifest_refs,
                "per_scope": per_scope,
                "segments": segment_rows,
                "units": compatibility_units,
            }
            atomic_write_json(project.paths["synthesis_trace"], trace)

        _emit(
            on_event,
            SynthesisEvent(
                "complete",
                completed=len(items),
                total=len(items),
                details={
                    "profile_id": profile.profile_id,
                    "reused": render_counts["reused"],
                    "rendered": render_counts["rendered"],
                    "activated": activate,
                    "manifests": len(manifest_refs),
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
                {"scope_id": work["scope"].id, "plan_id": work["plan"].plan_id}
                for work in included_work
            ],
            "scope": only_scope,
            "reused": render_counts["reused"],
            "rendered": render_counts["rendered"],
            "artifacts": artifacts,
            "manifests": tuple(manifest_refs),
            "activated": activate,
        }


def _group_items(
    items: list[dict[str, Any]],
) -> dict[tuple[str, str, int], list[dict[str, Any]]]:
    groups: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    next_run_by_scope: dict[str, int] = {}
    previous_by_scope: dict[str, tuple[str, int, int]] = {}
    for item in items:
        scope_id = item["scope_id"]
        language = item["language"]
        source_order = item["source_order"]
        previous = previous_by_scope.get(scope_id)
        if previous is not None and previous[0] == language and previous[1] + 1 == source_order:
            run_index = previous[2]
        else:
            run_index = next_run_by_scope.get(scope_id, 0)
            next_run_by_scope[scope_id] = run_index + 1
        key = (scope_id, language, run_index)
        groups.setdefault(key, []).append(item)
        previous_by_scope[scope_id] = (language, source_order, run_index)
    return groups


def _make_voice_render_event_handler(
    *,
    scope_id: str,
    language: str,
    items_by_locator: Mapping[str, Mapping[str, Any]],
    progress: RenderProgressState,
    scope_completed_before: int,
    group_progress: dict[str, int],
    render_counts: dict[str, int],
    group_counts: dict[str, int],
    counts_by_scope: dict[str, dict[str, int]],
    on_event: Callable[[SynthesisEvent], None] | None,
) -> Callable[[Any], None]:
    def handle(event: Any) -> None:
        kind = getattr(event.kind, "value", str(event.kind))
        if kind in {
            "preflight_started",
            "preflight_finished",
            "engine_open_started",
            "engine_open_finished",
        }:
            _emit(
                on_event,
                SynthesisEvent(
                    kind,
                    scope_id=scope_id,
                    completed=progress.completed,
                    total=progress.total,
                    details={"phase": "voicerender", "language": language},
                ),
            )
            return
        item = items_by_locator.get(getattr(event, "locator", None))
        if item is None:
            return
        if kind == "segment_started":
            _emit(
                on_event,
                SynthesisEvent(
                    "segment_started",
                    scope_id=scope_id,
                    unit_id=item["flow_id"],
                    unit_index=item["flow_index"],
                    segment_id=item["segment_id"],
                    segment_index=item["segment_index"],
                    completed=progress.completed,
                    total=progress.total,
                    text=item["segment"].text,
                    details={
                        **_synthesis_progress_details(
                            progress,
                            scope_id=scope_id,
                            scope_metadata=item["scope_metadata"],
                            scope_completed=scope_completed_before + group_progress["completed"],
                        ),
                        "source_locator": item["locator"],
                    },
                ),
            )
        elif kind in {"segment_rendered", "segment_reused"}:
            group_progress["completed"] += 1
            progress.completed += 1
            progress.scope_completed[scope_id] = (
                scope_completed_before + group_progress["completed"]
            )
            status = "rendered" if kind == "segment_rendered" else "reused"
            render_counts[status] += 1
            group_counts[status] += 1
            counts_by_scope[scope_id][status] += 1
            _emit(
                on_event,
                SynthesisEvent(
                    "segment_finished",
                    scope_id=scope_id,
                    unit_id=item["flow_id"],
                    unit_index=item["flow_index"],
                    segment_id=item["segment_id"],
                    segment_index=item["segment_index"],
                    completed=progress.completed,
                    total=progress.total,
                    text=item["segment"].text,
                    details={
                        "voicerender_status": kind,
                        "source_locator": item["locator"],
                        **_synthesis_progress_details(
                            progress,
                            scope_id=scope_id,
                            scope_metadata=item["scope_metadata"],
                            scope_completed=progress.scope_completed[scope_id],
                        ),
                    },
                ),
            )
        elif kind == "segment_failed":
            _emit(
                on_event,
                SynthesisEvent(
                    "segment_failed",
                    scope_id=scope_id,
                    unit_id=item["flow_id"],
                    unit_index=item["flow_index"],
                    segment_id=item["segment_id"],
                    segment_index=item["segment_index"],
                    completed=progress.completed,
                    total=progress.total,
                    text=item["segment"].text,
                    details={
                        "error_type": getattr(event, "error_type", None),
                        "error_code": getattr(event, "error_code", None),
                        "source_locator": item["locator"],
                    },
                ),
            )

    return handle


__all__ = [
    "SynthesisArtifact",
    "SynthesisEvent",
    "SynthesisProfile",
    "synthesis_profile_id",
    "synthesize_project",
]
