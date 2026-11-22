"""Distribution metadata shared by diagnostics and capability reports."""

from __future__ import annotations
from importlib import metadata

SPECIALIST_PACKAGES = (
    "ssmdconvert",
    "utterplan",
    "voicerender",
    "audiocompose",
    "audioexport",
    "renderplan",
)


def distribution_version(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def specialist_versions() -> dict[str, str | None]:
    return {package: distribution_version(package) for package in SPECIALIST_PACKAGES}
