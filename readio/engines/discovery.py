"""Engine-neutral target discovery."""

from __future__ import annotations

from .catalog import CatalogRequest, CatalogResult, SynthesisTarget
from .registry import CANONICAL_ENGINE_IDS, engine_ids, get_engine, normalize_engine_id


def discover_targets(
    *,
    engine: str | None = None,
    language: str | None = None,
    offline: bool = False,
    refresh: bool = False,
    preference: str = "auto",
) -> CatalogResult:
    """Discover synthesis targets through registered engine adapters.

    An explicit engine is authoritative and raises the registry's useful
    unavailable-engine error.  When no engine is selected, optional engines
    that cannot be imported are skipped so installed engines remain usable.
    """
    canonical = normalize_engine_id(engine) if engine is not None else None
    request = CatalogRequest(
        engine=canonical,
        language=language,
        offline=offline,
        refresh=refresh,
        preference=preference,
    )
    adapters = []
    if canonical is not None:
        adapters.append(get_engine(canonical))
    else:
        for engine_id in sorted(CANONICAL_ENGINE_IDS | frozenset(engine_ids())):
            try:
                adapters.append(get_engine(engine_id))
            except (ImportError, ValueError):
                continue

    targets: list[SynthesisTarget] = []
    for adapter in adapters:
        try:
            discovered = adapter.discover(request)
        except ImportError:
            if canonical is not None:
                raise
            continue
        targets.extend(discovered or ())

    return CatalogResult(
        targets=tuple(targets),
        registry_source="engine-adapters",
        offline=offline,
        refreshed=refresh,
    )


__all__ = ["discover_targets"]
