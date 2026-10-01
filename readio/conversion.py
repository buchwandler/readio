from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ssmdconvert import (
    MissingDependencyError,
    SSMDConvertError,
    UnsupportedInputError,
    convert,
)
from ssmdconvert import __version__ as ssmdconvert_version

from .errors import InputError


@dataclass(frozen=True, slots=True)
class ConvertedDocument:
    source: Path
    source_format: str
    media_type: str | None
    source_name: str
    ssmd: str
    metadata: Mapping[str, Any]
    converter_version: str


def convert_document_source(source: Path) -> ConvertedDocument:
    path = source.expanduser().resolve()
    is_book_bundle = (path.is_dir() and path.name.casefold().endswith(".ssmdbook")) or (
        path.is_file() and path.name.casefold().endswith(".ssmdbook.zip")
    )
    if is_book_bundle:
        raise InputError(
            "book bundles are multi-chapter inputs; use `readio audiobook init`",
            source_path=path,
            code="input.book_bundle_requires_audiobook",
        )
    if not path.is_file():
        raise InputError(
            f"document source is not a file: {path}",
            source_path=path,
            code="input.not_found",
        )

    try:
        result = convert(path)
    except UnsupportedInputError as exc:
        raise InputError(
            f"unsupported document input: {path.name}",
            source_path=path,
            code="input.format_unsupported",
        ) from exc
    except MissingDependencyError as exc:
        raise InputError(
            str(exc),
            source_path=path,
            code="input.converter_dependency_missing",
        ) from exc
    except SSMDConvertError as exc:
        raise InputError(
            f"could not convert {path.name}: {exc}",
            source_path=path,
            code="input.conversion_failed",
        ) from exc
    except (OSError, UnicodeError, ValueError) as exc:
        raise InputError(
            f"could not convert {path.name}: {exc}",
            source_path=path,
            code="input.conversion_failed",
        ) from exc

    source_info = result.document.source
    return ConvertedDocument(
        source=path,
        source_format=source_info.format,
        media_type=source_info.media_type,
        source_name=source_info.name or path.name,
        ssmd=result.ssmd,
        metadata=dict(result.document.metadata),
        converter_version=ssmdconvert_version,
    )
