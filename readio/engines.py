"""Engine and model management delegated to VoiceRender's public API."""

from __future__ import annotations

from pathlib import Path

import voicerender  # type: ignore[import-not-found]


def list_engines() -> list[dict[str, object]]:
    return voicerender.list_engines()


def inspect_engine(engine: str) -> dict[str, object]:
    return voicerender.engine_status(engine)


def discover_engine_targets(
    engine: str,
    *,
    language: str | None = None,
    offline: bool = False,
    refresh: bool = False,
    preference: str = "auto",
) -> tuple[object, ...]:
    return voicerender.discover_targets(
        engine,
        language=language,
        offline=offline,
        refresh=refresh,
        preference=preference,
    )


def list_engine_models(
    engine: str,
    *,
    language: str | None = None,
    installed: bool | None = None,
    available: bool = False,
    offline: bool = False,
    refresh: bool = False,
) -> tuple[object, ...]:
    if installed is not None and available:
        raise ValueError("installed and available filters are mutually exclusive")
    selected = False if available else installed
    return voicerender.list_models(
        engine,
        language=language,
        installed=selected,
        offline=offline,
        refresh=refresh,
    )


def inspect_engine_model(
    target: str,
    *,
    engine: str,
    quality: str | None = None,
) -> object:
    return voicerender.model_status(engine, target, quality=quality)


def install_engine_model(
    target: str,
    *,
    engine: str,
    quality: str | None = None,
    source: str | None = None,
    cache_dir: str | Path | None = None,
    force: bool = False,
) -> object:
    return voicerender.install_model(
        voicerender.ModelInstallRequest(
            engine=engine,
            target_id=target,
            quality=quality,
            source=source,
            cache_dir=Path(cache_dir) if cache_dir is not None else None,
            force=force,
        )
    )


def remove_engine_model(
    target: str,
    *,
    engine: str,
    quality: str | None = None,
    cache_dir: str | Path | None = None,
) -> object:
    return voicerender.remove_model(
        voicerender.ModelRemoveRequest(
            engine=engine,
            target_id=target,
            quality=quality,
            cache_dir=Path(cache_dir) if cache_dir is not None else None,
        )
    )


def verify_engine_model(
    target: str,
    *,
    engine: str,
    quality: str | None = None,
    cache_dir: str | Path | None = None,
) -> object:
    return voicerender.verify_model(
        engine,
        target,
        quality=quality,
        cache_dir=Path(cache_dir) if cache_dir is not None else None,
    )


def list_engine_options(engine: str, *, target: str | None = None) -> tuple[object, ...]:
    return voicerender.engine_options(engine, target_id=target)


__all__ = [
    "discover_engine_targets",
    "inspect_engine",
    "inspect_engine_model",
    "install_engine_model",
    "list_engine_models",
    "list_engine_options",
    "list_engines",
    "remove_engine_model",
    "verify_engine_model",
]
