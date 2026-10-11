"""Thin adapter over VoiceRender's stable public project-rendering contracts."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from voicerender import (
    CatalogResult,
    EngineRequest,
    EngineSelection,
    EngineUnavailableError,
    RenderEvent,
    RenderResult,
    SynthesisTarget,
    UtterPlanV5,
    VoiceBinding,
    VoiceProfile,
    load_source_plan,
    read_render_manifest,
    render_utterplan,
    resolve_voice,
    select_segments,
)
from voicerender import discover_targets as voice_render_discover_targets
from voicerender import engine_status as voice_render_engine_status
from voicerender import list_engines as voice_render_list_engines


@dataclass(frozen=True, slots=True)
class ResolvedVoiceProfile:
    profile: VoiceProfile
    selections: Mapping[str, EngineSelection]
    diagnostics: tuple[Any, ...] = ()



def catalog_engines() -> tuple[Mapping[str, Any], ...]:
    """List engine packages through VoiceRender without importing their SDKs."""
    return tuple(dict(item) for item in voice_render_list_engines())


def catalog_engine_status(engine: str) -> Mapping[str, Any]:
    """Return VoiceRender's structured public API probe for one engine."""
    return dict(voice_render_engine_status(engine))


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
        (engine,)
        if engine is not None
        else tuple(str(item["id"]) for item in catalog_engines())
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


def resolve_profile(
    bindings: Mapping[str, Mapping[str, Any]],
    *,
    name: str,
    default_role: str,
    language: str,
) -> ResolvedVoiceProfile:
    """Resolve role bindings through VoiceRender and return its native profile type."""
    resolved: dict[str, VoiceBinding] = {}
    selections: dict[str, EngineSelection] = {}
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
        selection, found_diagnostics = resolve_voice(request)
        diagnostics.extend(found_diagnostics)
        errors = [
            item for item in found_diagnostics if getattr(item, "severity", None) == "error"
        ]
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
    )


def render_plan(
    plan: Any,
    *,
    profile: VoiceProfile,
    output_dir: Path,
    cache_dir: Path,
    locators: Sequence[str],
    on_event: Callable[[RenderEvent], None] | None = None,
    resume: bool = True,
    force: bool = False,
) -> RenderBundle:
    """Render selected UtterPlan v5 source locators using VoiceRender."""
    source_plan = plan if isinstance(plan, UtterPlanV5) else load_source_plan(plan)
    selection = select_segments(source_plan, select=tuple(locators))
    result = render_utterplan(
        plan,
        profile=profile,
        output_dir=output_dir,
        cache_dir=cache_dir,
        selection=selection,
        resume=resume,
        force=force,
        on_event=on_event,
    )
    manifest = read_render_manifest(result.manifest_path)
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
