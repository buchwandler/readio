"""SSMDConvert public source inspection adapters for workflow orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import ssmdconvert  # type: ignore[import-not-found]


def inspect_source(source: str | Path) -> Any:
    """Inspect a source or existing bundle through SSMDConvert's public API."""
    return ssmdconvert.inspect_bundle_source(source)


def inspect_source_provenance(source: str | Path, bundle: str | Path) -> Any:
    """Compare source identity with an existing bundle through SSMDConvert."""
    inspector = getattr(ssmdconvert, "inspect_bundle_source_provenance", None)
    if not callable(inspector):
        raise TypeError(
            "This SSMDConvert version does not expose read-only source provenance inspection."
        )
    return inspector(source, bundle)
