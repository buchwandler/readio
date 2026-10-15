"""Thin adapter over VoiceRender's stable public project-rendering contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from voicerender import (
    CatalogResult,
    EngineRequest,
    EngineApiIncompatibleError,
    EngineUnavailableError,
    RenderEvent,
    RenderResult,
    SynthesisTarget,
    VoiceBinding,
    VoiceProfile,
    resolve_voice,
)
from voicerender import discover_targets as voice_render_discover_targets
from voicerender import engine_status as voice_render_engine_status
from voicerender import list_engines as voice_render_list_engines


@dataclass(frozen=True, slots=True)
class ResolvedVoiceProfile:
    profile: VoiceProfile
    selections: Mapping[str, Any]
    diagnostics: tuple[Any, ...] = ()
    adapters: Mapping[str, Any] = field(default_factory=dict)


def catalog_engines() -> tuple[Mapping[str, Any], ...]:
    """List engine packages through VoiceRender without importing their SDKs."""
    return tuple(dict(item) for item in voice_render_list_engines())


def catalog_engine_status(engine: str) -> Mapping[str, Any]:
    """Return VoiceRender's structured public API probe for one engine."""
    return dict(voice_render_engine_status(engine))


def _discover_readio_targets(
    engine: str,
    *,
    language: str | None,
    offline: bool,
    refresh: bool,
    preference: str,
) -> tuple[Any, ...]:
    """Provide compatibility catalogs for Readio-registered test/plugin engines."""
    from ..engines.catalog import CatalogRequest as ReadioCatalogRequest
    from ..engines.registry import get_engine
    from ..engines.selection import EngineRequest as ReadioEngineRequest

    adapter = get_engine(engine)
    request = ReadioCatalogRequest(
        engine=engine,
        language=language,
        offline=offline,
        refresh=refresh,
        preference=preference,
    )
    discover = getattr(adapter, "discover", None)
    targets = tuple(discover(request)) if callable(discover) else ()
    if targets:
        return targets
    selection, _diagnostics = adapter.resolve(
        ReadioEngineRequest(engine=engine, language=language, offline=offline, refresh=refresh)
    )
    metadata_method = getattr(adapter, "target_metadata", None)
    metadata = dict(metadata_method(selection)) if callable(metadata_method) else {}
    voices = tuple(metadata.get("voices", ()))
    if not voices and selection.voice:
        voices = (selection.voice,)
    from ..engines.catalog import SynthesisTarget as ReadioSynthesisTarget

    return (
        ReadioSynthesisTarget(
            engine=selection.engine,
            id=selection.target_id,
            display_name=str(metadata.get("display_name") or selection.target_id),
            languages=tuple(metadata.get("languages", ()))
            or ((selection.language,) if selection.language else ()),
            voices=voices,
            default_voice=selection.voice,
            metadata=metadata,
        ),
    )


def discover_catalog_targets(
    engine: str | None = None,
    *,
    language: str | None = None,
    offline: bool = False,
    refresh: bool = False,
    preference: str = "auto",
) -> CatalogResult:
    """Discover one or all target catalogs through VoiceRender's public API."""
    engine_ids = (
        (engine,) if engine is not None else tuple(str(item["id"]) for item in catalog_engines())
    )
    targets: list[SynthesisTarget] = []
    for engine_id in engine_ids:
        try:
            targets.extend(
                voice_render_discover_targets(
                    engine_id,
                    language=language,
                    offline=offline,
                    refresh=refresh,
                    preference=preference,
                )
            )
        except EngineUnavailableError:
            if engine is not None:
                raise
        except Exception as error:
            if "Unknown engine" not in str(error):
                raise
            try:
                targets.extend(
                    _discover_readio_targets(
                        engine_id,
                        language=language,
                        offline=offline,
                        refresh=refresh,
                        preference=preference,
                    )
                )
            except (TypeError, ValueError):
                raise error
    return CatalogResult(
        targets=tuple(targets),
        registry_source="voicerender",
        offline=offline,
        refreshed=refresh,
    )


_ENGINE_ALIASES = {
    "pykokoro": "kokoro",
    "pipersynth": "piper",
    "pocketsynth": "pocket",
    "kittensynth": "kitten",
    "supertonicsynth": "supertonic",
    "inflectsynth": "inflect",
}


def normalize_engine_id(engine: str) -> str:
    """Normalize Readio's documented engine aliases without loading an engine SDK."""
    normalized = engine.strip().casefold()
    return _ENGINE_ALIASES.get(normalized, normalized)


@dataclass(frozen=True, slots=True)
class RenderBundle:
    result: RenderResult
    manifest: Mapping[str, Any]


_NON_ACOUSTIC_OPTIONS = frozenset({"cache_dir", "force_download", "catalog_url"})


def _acoustic_options(options: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in options.items() if key not in _NON_ACOUSTIC_OPTIONS}


def _readio_engine_adapter(engine: str | None) -> Any | None:
    from ..engines.registry import get_engine

    if not engine:
        return None
    try:
        return get_engine(engine)
    except (TypeError, ValueError):
        return None


def _resolve_readio_request(
    request: EngineRequest, adapter: Any | None = None
) -> tuple[Any, tuple[Any, ...], Any]:
    from ..engines.selection import EngineRequest as ReadioEngineRequest

    adapter = adapter or _readio_engine_adapter(request.engine)
    if adapter is None:
        raise ValueError(f"No Readio engine adapter is registered for {request.engine!r}")
    readio_request = ReadioEngineRequest(
        engine=request.engine,
        target_id=request.target_id,
        language=request.language,
        voice=request.voice,
        speaker=request.speaker,
        options=dict(request.options),
        offline=bool(request.offline),
        refresh=bool(request.refresh),
        engine_options=dict(request.engine_options),
    )
    selection, diagnostics = adapter.resolve(readio_request)
    return selection, tuple(diagnostics), adapter


def _resolve_voice_request(request: EngineRequest) -> tuple[Any, tuple[Any, ...], Any | None]:
    try:
        selection, diagnostics = resolve_voice(request)
    except Exception:
        adapter = _readio_engine_adapter(request.engine)
        if adapter is None:
            raise
        return _resolve_readio_request(request, adapter)
    diagnostics = tuple(diagnostics)
    if not any(getattr(item, "severity", None) == "error" for item in diagnostics):
        return selection, diagnostics, None
    adapter = _readio_engine_adapter(request.engine)
    if adapter is not None:
        return _resolve_readio_request(request, adapter)
    return selection, diagnostics, None


def resolve_profile(
    bindings: Mapping[str, Mapping[str, Any]],
    *,
    name: str,
    default_role: str,
    language: str,
) -> ResolvedVoiceProfile:
    """Resolve package-native roles, retaining compatibility with Readio engines."""
    resolved: dict[str, VoiceBinding] = {}
    selections: dict[str, Any] = {}
    adapters: dict[str, Any] = {}
    diagnostics: list[Any] = []
    for role, binding in bindings.items():
        request = EngineRequest(
            engine=binding.get("engine"),
            target_id=binding.get("target_id"),
            language=binding.get("language", language),
            voice=binding.get("voice"),
            speaker=binding.get("speaker"),
            options=dict(binding.get("options", {})),
            offline=bool(binding.get("offline", False)),
            refresh=bool(binding.get("refresh", False)),
            engine_options=dict(binding.get("engine_options", {})),
        )
        selection, found_diagnostics, adapter = _resolve_voice_request(request)
        diagnostics.extend(found_diagnostics)
        errors = [item for item in found_diagnostics if getattr(item, "severity", None) == "error"]
        if errors:
            raise ValueError("; ".join(f"{item.code}: {item.message}" for item in errors))
        reference_audio = binding.get("reference_audio")
        revision = binding.get("model_revision")
        resolved[role] = VoiceBinding(
            engine=selection.engine,
            target_id=selection.target_id,
            voice=selection.voice,
            speaker=selection.speaker,
            reference_audio=(Path(reference_audio) if reference_audio is not None else None),
            options=dict(selection.options),
            model_revision=(str(revision) if revision is not None else None),
        )
        selections[role] = selection
        if adapter is not None:
            adapters[role] = adapter
    if not resolved:
        raise ValueError("VoiceRender profile requires at least one role binding")
    if default_role not in resolved:
        raise ValueError(f"VoiceRender profile default role {default_role!r} is not configured")
    return ResolvedVoiceProfile(
        VoiceProfile(
            name=name,
            roles=resolved,
            defaults={"role": default_role, "language": language},
        ),
        selections,
        tuple(diagnostics),
        adapters,
    )


def render_plan(
    plan: Any,
    *,
    profile: VoiceProfile | ResolvedVoiceProfile,
    output_dir: Path,
    cache_dir: Path,
    locators: Sequence[str],
    on_event: Callable[[RenderEvent], None] | None = None,
    resume: bool = True,
    force: bool = False,
) -> RenderBundle:
    """Render selected schema-v7 segments through VoiceRender's request API."""
    from contextlib import ExitStack
    from dataclasses import asdict

    import numpy as np
    from utterplan import FlowPlan
    from voicerender import SpeechRequest, open_session

    from ..audioio import probe_audio, write_pcm16_wav

    source_path = Path(plan) if isinstance(plan, (str, Path)) else None
    flow_plan = FlowPlan.load(source_path) if source_path is not None else plan
    if not isinstance(flow_plan, FlowPlan):
        raise TypeError("generic project synthesis requires an UtterPlan FlowPlan")
    resolved_profile = profile if isinstance(profile, ResolvedVoiceProfile) else None
    voice_profile = resolved_profile.profile if resolved_profile is not None else profile

    selected_locators = tuple(locators)
    if not selected_locators:
        raise ValueError("VoiceRender selection must include at least one source locator")
    default_role = voice_profile.default_role or next(iter(voice_profile.roles))
    segments_by_locator: dict[str, tuple[Any, Any, str, Any]] = {}
    from ..planning.flow import iter_flow_segments

    for segment_ref in iter_flow_segments(flow_plan):
        flow, segment = segment_ref.flow, segment_ref.segment
        locator = segment_ref.id
        voice = getattr(getattr(segment, "directives", None), "voice", None)
        role = getattr(voice, "reference", None) or default_role
        if role not in voice_profile.roles:
            role = default_role
        segments_by_locator[locator] = (flow, segment, role, segment_ref)
    missing = [locator for locator in selected_locators if locator not in segments_by_locator]
    if missing:
        raise ValueError(f"VoiceRender selection contains unknown source locators: {missing!r}")

    output_dir = Path(output_dir)
    cache_dir = Path(cache_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    source_bytes = (
        source_path.read_bytes() if source_path is not None else flow_plan.to_toml().encode("utf-8")
    )
    profile_payload = {
        "name": voice_profile.name,
        "defaults": dict(voice_profile.defaults),
        "roles": {
            role: {
                "engine": binding.engine,
                "target_id": binding.target_id,
                "voice": binding.voice,
                "speaker": binding.speaker,
                "options": _acoustic_options(binding.options),
                "model_revision": binding.model_revision,
            }
            for role, binding in sorted(voice_profile.roles.items())
        },
    }
    profile_digest = hashlib.sha256(
        json.dumps(profile_payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    entries: list[dict[str, Any]] = []
    rendered_count = reused_count = 0
    session_cache: dict[str, Any] = {}
    selections_by_role = dict(resolved_profile.selections) if resolved_profile is not None else {}
    adapters_by_role = dict(resolved_profile.adapters) if resolved_profile is not None else {}
    if on_event is not None:
        on_event(SimpleNamespace(kind="preflight_started", locator=None, details={}))

    with ExitStack() as stack:
        for locator in selected_locators:
            flow, segment, role, segment_ref = segments_by_locator[locator]
            selection = selections_by_role.get(role)
            if selection is None:
                binding = voice_profile.roles[role]
                request = EngineRequest(
                    engine=binding.engine,
                    target_id=binding.target_id,
                    language=segment.language,
                    voice=binding.voice,
                    speaker=binding.speaker,
                    options=dict(binding.options),
                )
                selection, diagnostics, adapter = _resolve_voice_request(request)
                if adapter is not None:
                    adapters_by_role[role] = adapter
                errors = [
                    item for item in diagnostics if getattr(item, "severity", None) == "error"
                ]
                if errors:
                    raise ValueError("; ".join(f"{item.code}: {item.message}" for item in errors))
                selections_by_role[role] = selection
            selection_payload = {
                "engine": selection.engine,
                "target_id": selection.target_id,
                "language": selection.language,
                "voice": selection.voice,
                "speaker": selection.speaker,
                "options": _acoustic_options(selection.options),
                "metadata": dict(selection.metadata),
            }
            identity_payload = {
                "selection": selection_payload,
                "text": segment.text,
                "language": segment.language,
            }
            cache_key = hashlib.sha256(
                json.dumps(
                    identity_payload, sort_keys=True, separators=(",", ":"), default=str
                ).encode()
            ).hexdigest()
            cache_wav = cache_dir / f"{cache_key}.wav"
            cache_metadata = cache_dir / f"{cache_key}.json"
            output_wav = output_dir / "segments" / f"{locator.replace('.', '-')}.wav"
            output_wav.parent.mkdir(parents=True, exist_ok=True)
            timing_spans: list[dict[str, Any]] = []
            status = "rendered"
            atomic_manifest: Mapping[str, Any] | None = None
            audio_info = None
            audio_sha256 = ""
            if resume and not force and cache_wav.is_file() and cache_metadata.is_file():
                try:
                    cached = json.loads(cache_metadata.read_text(encoding="utf-8"))
                    cache_bytes = cache_wav.read_bytes()
                    if cached.get("cache_key") == cache_key and hashlib.sha256(
                        cache_bytes
                    ).hexdigest() == cached.get("sha256"):
                        output_wav.write_bytes(cache_bytes)
                        timing_spans = list(cached.get("timing_spans", []))
                        audio_sha256 = cached["sha256"]
                        audio_info = probe_audio(output_wav)
                        status = "reused"
                except (OSError, ValueError, KeyError, TypeError):
                    status = "rendered"
            if on_event is not None:
                on_event(
                    SimpleNamespace(
                        kind="segment_started",
                        locator=locator,
                        text=segment.text,
                        details={},
                    )
                )
            if status != "reused":
                try:
                    session_key = json.dumps(
                        selection_payload, sort_keys=True, separators=(",", ":"), default=str
                    )
                    session = session_cache.get(session_key)
                    adapter = adapters_by_role.get(role)
                    if session is None:
                        if adapter is None:
                            try:
                                session = stack.enter_context(open_session(selection))
                            except (EngineApiIncompatibleError, EngineUnavailableError):
                                binding = voice_profile.roles[role]
                                fallback_request = EngineRequest(
                                    engine=binding.engine,
                                    target_id=binding.target_id,
                                    language=segment.language or selection.language,
                                    voice=binding.voice,
                                    speaker=binding.speaker,
                                    options=dict(binding.options),
                                )
                                selection, diagnostics, adapter = _resolve_readio_request(
                                    fallback_request
                                )
                                errors = [
                                    item
                                    for item in diagnostics
                                    if getattr(item, "severity", None) == "error"
                                ]
                                if errors:
                                    raise ValueError(
                                        "; ".join(f"{item.code}: {item.message}" for item in errors)
                                    )
                                adapters_by_role[role] = adapter
                                selections_by_role[role] = selection
                                session = stack.enter_context(adapter.open(selection))
                        else:
                            session = stack.enter_context(adapter.open(selection))
                        session_cache[session_key] = session
                    if adapter is None:
                        speech_request = SpeechRequest(
                            id=locator,
                            text=segment.text,
                            language=selection.language or segment.language,
                            voice=selection.voice,
                            speaker=selection.speaker,
                            options=dict(selection.options),
                        )
                        rendered = session.synthesize(speech_request)
                    else:
                        from ..rendering.capacity import CapacityContext, render_atomic_request
                        from ..rendering.lowering import lower_segment

                        lowered = lower_segment(
                            flow_plan,
                            segment_ref,
                            selection,
                            adapter.capabilities(),
                        )
                        atomic = render_atomic_request(
                            session,
                            lowered.request,
                            capacity=CapacityContext(
                                semantic_boundaries=lowered.semantic_boundaries,
                                linguistic_token_ranges=lowered.capacity_token_ranges,
                                planned_semantics=True,
                            ),
                        )
                        rendered = atomic.result
                        atomic_manifest = atomic.manifest
                    write_pcm16_wav(
                        output_wav,
                        np.asarray(rendered.audio, dtype=np.float32),
                        int(rendered.sample_rate),
                    )
                    audio_info = probe_audio(output_wav)
                    audio_bytes = output_wav.read_bytes()
                    audio_sha256 = hashlib.sha256(audio_bytes).hexdigest()
                    timing_spans = [asdict(item) for item in rendered.word_timings]
                    cache_wav.write_bytes(audio_bytes)
                    cache_metadata.write_text(
                        json.dumps(
                            {
                                "cache_key": cache_key,
                                "sha256": audio_sha256,
                                "timing_spans": timing_spans,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                except Exception as error:
                    if on_event is not None:
                        on_event(
                            SimpleNamespace(
                                kind="segment_failed",
                                locator=locator,
                                error_type=type(error).__name__,
                                error_code=getattr(error, "code", None),
                                details={},
                            )
                        )
                    raise
            if audio_info is None:
                raise ValueError(f"VoiceRender returned no audio artifact for {locator!r}")
            if status == "reused":
                reused_count += 1
            else:
                rendered_count += 1
            source_semantics = json.dumps(
                segment.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            semantic_hash = hashlib.sha256(
                flow.content_hash.encode("utf-8") + b":" + source_semantics
            ).hexdigest()
            entries.append(
                {
                    "source": {"locator": locator, "semantic_segment_hash": semantic_hash},
                    "status": status,
                    "audio": {
                        "path": output_wav.relative_to(output_dir).as_posix(),
                        "sha256": audio_sha256,
                        "sample_rate": audio_info.sample_rate,
                        "channels": audio_info.channels,
                        "frames": audio_info.frames,
                        "encoding": "PCM_16",
                    },
                    "acoustic_cache_key": f"sha256:{cache_key}",
                    "synthesis_semantics_sha256": f"sha256:{cache_key}",
                    **({"readio_atomic": atomic_manifest} if atomic_manifest is not None else {}),
                    "timing": {"spans": timing_spans},
                }
            )
            if on_event is not None:
                on_event(
                    SimpleNamespace(
                        kind="segment_reused" if status == "reused" else "segment_rendered",
                        locator=locator,
                        text=segment.text,
                        details={},
                    )
                )

    manifest: dict[str, Any] = {
        "schema": "readio.voicerender-compat.v1",
        "source": {
            "plan_id": flow_plan.plan_id,
            "plan_file_sha256": f"sha256:{hashlib.sha256(source_bytes).hexdigest()}",
        },
        "profile": {"effective_profile_sha256": profile_digest},
        "selection": {"locators": list(selected_locators)},
        "segments": entries,
    }
    manifest["manifest_id"] = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    manifest_path = output_dir / "readio-manifest.json"
    temporary_path = manifest_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(manifest_path)
    if on_event is not None:
        on_event(SimpleNamespace(kind="preflight_finished", locator=None, details={}))
    result = RenderResult(
        manifest_path=manifest_path,
        rendered_count=rendered_count,
        reused_count=reused_count,
        failed_count=0,
        selected_count=len(entries),
    )
    return RenderBundle(result=result, manifest=manifest)


__all__ = [
    "RenderBundle",
    "ResolvedVoiceProfile",
    "catalog_engine_status",
    "catalog_engines",
    "discover_catalog_targets",
    "normalize_engine_id",
    "render_plan",
    "resolve_profile",
]
