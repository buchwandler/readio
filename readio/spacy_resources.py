"""Readio-owned inspection and explicit provisioning of local spaCy models."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import Literal

from utterplan.spacy_models import resolve_spacy_model

SpacyTier = Literal["sm", "md", "lg", "trf"]


_SPACY_MODEL_TIERS: tuple[SpacyTier, ...] = ("sm", "md", "lg", "trf")


@dataclass(frozen=True, slots=True)
class SpacyModelInfo:
    language: str
    tier: SpacyTier
    model: str
    installed: bool
    spacy_version: str | None
    model_version: str | None


def _distribution_version(package: str) -> str | None:
    try:
        return version(package)
    except PackageNotFoundError:
        return None


def _load_model(model: str) -> None:
    spacy = import_module("spacy")
    spacy.load(model)


def list_spacy_models(language: str) -> tuple[SpacyModelInfo, ...]:
    """List concrete spaCy model packages using distribution metadata only."""
    spacy_version = _distribution_version("spacy")
    models: list[SpacyModelInfo] = []
    for tier in _SPACY_MODEL_TIERS:
        model = resolve_spacy_model(language, tier)
        model_version = _distribution_version(model)
        models.append(
            SpacyModelInfo(
                language=language,
                tier=tier,
                model=model,
                installed=model_version is not None,
                spacy_version=spacy_version,
                model_version=model_version,
            )
        )
    return tuple(models)


def inspect_spacy_model(language: str, tier: SpacyTier) -> SpacyModelInfo:
    """Inspect whether the resolved local model is installed and loadable."""
    model = resolve_spacy_model(language, tier)
    spacy_version = _distribution_version("spacy")
    model_version = _distribution_version(model)
    installed = spacy_version is not None and model_version is not None
    if installed:
        _load_model(model)
    return SpacyModelInfo(
        language=language,
        tier=tier,
        model=model,
        installed=installed,
        spacy_version=spacy_version,
        model_version=model_version if installed else None,
    )


def ensure_spacy_model(
    language: str,
    tier: SpacyTier,
    *,
    offline: bool = False,
) -> SpacyModelInfo:
    """Return a loadable model, explicitly downloading it when allowed."""
    info = inspect_spacy_model(language, tier)
    if info.installed:
        return info
    if offline:
        raise RuntimeError(
            f"spaCy model {info.model!r} is not installed and offline mode is enabled"
        )
    if info.spacy_version is None:
        raise RuntimeError('spaCy is not installed; install Readio with the "spacy" extra first')

    subprocess.run([sys.executable, "-m", "spacy", "download", info.model], check=True)
    verified = inspect_spacy_model(language, tier)
    if not verified.installed:
        raise RuntimeError(
            f"spaCy model {verified.model!r} is still unavailable after provisioning"
        )
    return verified


__all__ = [
    "SpacyModelInfo",
    "ensure_spacy_model",
    "inspect_spacy_model",
    "list_spacy_models",
]
