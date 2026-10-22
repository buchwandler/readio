"""Resolve declarative intents against paths and specialist-owned bundle metadata."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from readio.errors import ReadioError
from readio.integrations.audioexport import supported_formats
from readio.intent import (
    RenderIntent,
    _default_output_path,
    validate_intent_shape,
    validate_render_intent,
)
from readio.workspace import (
    WorkspaceKind,
    default_bundle_workspace_path,
    inspect_source_kind,
    resolve_workspace,
)


@dataclass(frozen=True, slots=True)
class ResolvedRenderIntent:
    """Concrete paths and specialist-verified render choices for one intent."""

    intent: RenderIntent
    config_path: Path | None
    source: Path
    bundle: Path
    profile: Path
    kind: WorkspaceKind
    chapters: str
    language: str | None
    output_format: str
    output: Path


def _absolute_path(value: str | Path, *, base: Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def _is_bundle_path(path: Path) -> bool:
    name = path.name.casefold()
    if name.endswith(".ssmd-bundle.zip"):
        raise ValueError(
            "Readio requires an editable .ssmd-bundle directory; packed .ssmd-bundle.zip "
            "artifacts are not pipeline workspaces"
        )
    return name.endswith(".ssmd-bundle")


def _validate_bundle_path(path: Path) -> None:
    if not path.name.casefold().endswith(".ssmd-bundle"):
        raise ValueError(f"bundle path must end in .ssmd-bundle: {path}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"bundle target is not a directory: {path}")


def _require_writable_parent(path: Path, label: str) -> None:
    parent = path.parent
    if parent.exists() and not parent.is_dir():
        raise NotADirectoryError(f"{label} parent is not a directory: {parent}")
    candidate = parent
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    if not candidate.is_dir():
        raise FileNotFoundError(f"no existing parent directory for {label}: {parent}")
    if not os.access(candidate, os.W_OK | os.X_OK):
        raise PermissionError(f"{label} parent is not writable: {parent}")


def _resolved_kind(source: Path, bundle: Path) -> WorkspaceKind:
    if _is_bundle_path(source):
        return resolve_workspace(source).kind
    if bundle.exists():
        return resolve_workspace(bundle).kind
    kind = inspect_source_kind(source)
    if kind not in {"book", "document"}:
        raise ValueError(f"SSMDConvert returned unsupported source kind {kind!r}")
    return kind


def _format_extension(format_spec: object) -> str:
    extension = getattr(format_spec, "extension", None)
    if not isinstance(extension, str) or not extension.startswith("."):
        raise ReadioError("AudioExport returned a format without a valid file extension.")
    return extension.casefold()


def resolve_render_intent(
    intent: RenderIntent,
    *,
    config_path: str | Path | None = None,
    source: str | Path | None = None,
    bundle: str | Path | None = None,
    profile: str | Path | None = None,
    variant: str | None = None,
    chapters: str | None = None,
    language: str | None = None,
    output_format: str | None = None,
    output: str | Path | None = None,
    check_output_collision: bool = True,
    force_output: bool = False,
) -> ResolvedRenderIntent:
    """Resolve paths and verify profile, bundle kind, export format, and output policy.

    Relative paths stored in an intent resolve against its TOML file. Explicit
    path overrides resolve against the current working directory.
    """
    validate_intent_shape(intent)
    config = Path(config_path).expanduser().resolve() if config_path is not None else None
    config_dir = config.parent if config is not None else Path.cwd().resolve()
    cwd = Path.cwd().resolve()

    source_override = source is not None
    source_value = source if source_override else intent.input.source or intent.input.bundle
    if source_value is None:
        raise ValueError(
            "an input.source or input.bundle is required (or pass SOURCE on the command line)"
        )
    source_path = _absolute_path(source_value, base=cwd if source_override else config_dir)
    if not source_path.exists():
        raise FileNotFoundError(f"render source does not exist: {source_path}")

    source_is_bundle = _is_bundle_path(source_path)
    if source_is_bundle:
        if bundle is not None and _absolute_path(bundle, base=cwd) != source_path:
            raise ValueError("a bundle source cannot be redirected to a different bundle path")
        bundle_path = source_path
    elif bundle is not None:
        bundle_path = _absolute_path(bundle, base=cwd)
    elif intent.input.bundle is not None:
        bundle_path = _absolute_path(intent.input.bundle, base=config_dir)
    else:
        bundle_path = default_bundle_workspace_path(source_path).resolve()
    _validate_bundle_path(bundle_path)

    profile_override = profile is not None
    profile_value = profile if profile_override else intent.voice.profile
    if profile_value is None:
        raise ValueError("voice.profile is required (or pass --profile)")
    profile_path = _absolute_path(profile_value, base=cwd if profile_override else config_dir)

    chapters_value = chapters if chapters is not None else intent.input.chapters
    if chapters_value is None:
        chapters_value = "all"
    if language is not None:
        language_value = language
    else:
        language_value = intent.input.language

    actual_kind = _resolved_kind(source_path, bundle_path)
    if intent.kind is not None and intent.kind != actual_kind:
        raise ValueError(
            f"declared intent kind {intent.kind!r} does not match SSMDConvert kind "
            f"{actual_kind!r} for {bundle_path}"
        )

    if actual_kind == "document" and (chapters is not None or intent.input.chapters is not None):
        raise ValueError("input.chapters is not valid for document intents")
    if actual_kind == "document":
        chapters_value = "all"

    format_value = output_format if output_format is not None else intent.output.format
    if not isinstance(format_value, str) or not format_value.strip():
        raise ValueError("output.format must be a non-empty string")
    format_value = format_value.casefold()
    if format_value == "auto":
        format_value = "m4b" if actual_kind == "book" else "mp3"
    if actual_kind == "book" and format_value != "m4b":
        raise ValueError("the verified book export workflow supports only m4b output")

    try:
        formats = supported_formats()
    except ReadioError as error:
        raise ReadioError(f"Cannot preflight final output: {error}") from error
    format_spec = formats.get(format_value)
    if format_spec is None:
        supported = ", ".join(sorted(formats))
        raise ValueError(
            f"AudioExport does not support output format {format_value!r}; supported formats: {supported}"
        )

    if output is not None:
        output_path = _absolute_path(output, base=cwd)
    elif intent.output.path is not None:
        output_path = _absolute_path(intent.output.path, base=config_dir)
    else:
        output_path = _default_output_path(source_path, actual_kind, format_value).resolve()
    expected_extension = _format_extension(format_spec)
    if output_path.suffix.casefold() != expected_extension:
        raise ValueError(
            f"output path extension must be {expected_extension} for format {format_value!r}: "
            f"{output_path}"
        )
    if output_path.exists():
        if output_path.is_dir():
            raise IsADirectoryError(f"output path is a directory: {output_path}")
        if check_output_collision and not force_output:
            raise FileExistsError(
                f"output already exists: {output_path}; use the explicit export force option to replace it"
            )
    _require_writable_parent(output_path, "output")
    if not bundle_path.exists():
        _require_writable_parent(bundle_path, "bundle")

    effective_intent = replace(
        intent,
        kind=actual_kind,
        input=replace(
            intent.input,
            source=source_path,
            bundle=bundle_path,
            chapters=chapters_value if actual_kind == "book" else None,
            language=language_value,
        ),
        voice=replace(
            intent.voice,
            profile=profile_path,
            variant=variant if variant is not None else intent.voice.variant,
        ),
        output=replace(intent.output, format=format_value, path=output_path),
    )
    validate_render_intent(effective_intent)

    for label, path in (
        ("cover", effective_intent.output.cover),
        ("timeline", effective_intent.output.timeline),
    ):
        if path is not None:
            resolved_path = _absolute_path(path, base=config_dir)
            if not resolved_path.is_file():
                raise FileNotFoundError(f"output.{label} file does not exist: {resolved_path}")
            effective_intent = replace(
                effective_intent,
                output=replace(
                    effective_intent.output,
                    **{label: resolved_path},
                ),
            )

    return ResolvedRenderIntent(
        intent=effective_intent,
        config_path=config,
        source=source_path,
        bundle=bundle_path,
        profile=profile_path,
        kind=actual_kind,
        chapters=chapters_value,
        language=language_value,
        output_format=format_value,
        output=output_path,
    )


__all__ = ["ResolvedRenderIntent", "resolve_render_intent"]
