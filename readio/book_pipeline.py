"""Thin in-memory orchestration over the public audiobook package APIs.

The .ssmdbook workspace and its stage-owned artifacts are authoritative. This
module intentionally defines no persistent Readio book schema and never creates
workspace directories while opening a book.
"""

from __future__ import annotations

import hashlib
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib
from audioexport import export_book as _audioexport_export_book
from ssmdconvert import (
    Book as SSMDConvertBook,
)
from ssmdconvert import (
    BookChapter as SSMDConvertBookChapter,
)
from ssmdconvert import (
    BookConversionOptions,
    DocumentConversionOptions,
)
from ssmdconvert import (
    BookWorkspace as SSMDConvertWorkspace,
)
from ssmdconvert import (
    convert_book as _ssmdconvert_convert_book,
)
from ssmdconvert import (
    convert_document as _ssmdconvert_convert_document,
)
from ssmdconvert import (
    load_book_bundle as _ssmdconvert_load_book_bundle,
)
from ssmdconvert import (
    load_book_workspace as _ssmdconvert_load_book_workspace,
)
from ssmdconvert import (
    write_book_bundle as _ssmdconvert_write_book_bundle,
)
from utterplan import compile_book as _utterplan_compile_book
from utterplan import render_book as _utterplan_render_book
from voicerender import (
    create_render_profile as _voicerender_create_render_profile,
)
from voicerender import (
    render_book as _voicerender_render_book,
)
from voicerender import (
    save_render_profile as _voicerender_save_render_profile,
)

from .chapter_selection import parse_chapter_selection

ProgressHandler = Callable[["BookPipelineEvent"], None]
StopAfter = Literal["render-plan", "synth", "compose", "export"]


@dataclass(frozen=True, slots=True)
class BookPipelineEvent:
    """Presentation-only progress translated from a specialist package."""

    stage: str
    kind: str
    phase: str
    completed: int | None = None
    total: int | None = None
    chapter_id: str | None = None
    source_number: int | None = None
    variant: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


class BookPipelineError(Exception):
    """A package failure with its identity preserved for CLI/API presentation."""

    def __init__(
        self,
        *,
        package: str,
        code: str,
        message: str,
        stage: str,
        chapter_id: str | None = None,
        source_number: int | None = None,
        variant: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.package = package
        self.code = code
        self.message = message
        self.stage = stage
        self.chapter_id = chapter_id
        self.source_number = source_number
        self.variant = variant
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly structured error without flattening its code."""
        return {
            "package": self.package,
            "code": self.code,
            "message": self.message,
            "stage": self.stage,
            "chapter_id": self.chapter_id,
            "source_number": self.source_number,
            "variant": self.variant,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class BookWorkspace:
    """Read-only path view over an SSMDConvert-owned editable workspace."""

    root: Path
    manifest: Path
    chapters_dir: Path
    utterplan_dir: Path
    renderplan_dir: Path
    voicerender_dir: Path
    audiocompose_dir: Path
    audioexport_dir: Path
    native: SSMDConvertWorkspace = field(repr=False, compare=False)

    @property
    def dirty(self) -> bool:
        return self.native.dirty


@dataclass(frozen=True, slots=True)
class BookBuildRequest:
    """In-memory controls for a resumable audiobook build."""

    workspace: Path
    profile: Path
    chapters: str = "all"
    compile: bool = True
    project_renderplans: bool = True
    synthesize: bool = True
    compose: bool = True
    export: bool = True
    force: bool = False
    fail_fast: bool = False
    emphasis_policy: str = "defer-to-compose"
    variant: str | None = None
    stop_after: StopAfter | None = None
    export_profile: Path | None = None
    output: Path | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    cover: Path | None = None
    bitrate: str | int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", Path(self.workspace))
        object.__setattr__(self, "profile", Path(self.profile))
        if self.export_profile is not None:
            object.__setattr__(self, "export_profile", Path(self.export_profile))
        if self.output is not None:
            object.__setattr__(self, "output", Path(self.output))
        if self.cover is not None:
            object.__setattr__(self, "cover", Path(self.cover))
        object.__setattr__(self, "metadata", dict(self.metadata))
        if not isinstance(self.chapters, str) or not self.chapters.strip():
            raise ValueError("chapters must be a non-empty selector string")
        if self.stop_after not in {None, "render-plan", "synth", "compose", "export"}:
            raise ValueError("stop_after must be render-plan, synth, compose, export, or None")


@dataclass(frozen=True, slots=True)
class BookBuildResult:
    """Results from each stage actually run; no result is persisted by Readio."""

    workspace: Path
    chapters: str
    variant: str
    compile_result: object | None = None
    renderplan_result: object | None = None
    voicerender_result: object | None = None
    compose_result: object | None = None
    export_result: object | None = None


def _audiocompose_compose_book(*args: Any, **kwargs: Any) -> Any:
    """Resolve AudioCompose's public book API at call time."""
    import audiocompose

    operation = getattr(audiocompose, "compose_book", None)
    if not callable(operation):
        raise BookPipelineError(
            package="audiocompose",
            code="dependency.api_incompatible",
            message=(
                "Installed AudioCompose does not provide compose_book(); "
                "install a Readio-compatible AudioCompose release."
            ),
            stage="compose",
            details={"required_symbol": "compose_book"},
        )
    return operation(*args, **kwargs)


def _error_from_exception(
    error: Exception,
    *,
    package: str,
    stage: str,
    variant: str | None = None,
) -> BookPipelineError:
    details = getattr(error, "details", None)
    if not isinstance(details, Mapping):
        details = {}
    chapter_id = getattr(error, "chapter_id", None)
    source_number = getattr(error, "source_number", None)
    if chapter_id is None:
        chapter_id = details.get("chapter_id")
    if source_number is None:
        source_number = details.get("source_number")
    code = getattr(error, "code", None)
    if not isinstance(code, str) or not code:
        code = f"{package}.error"
    error_variant = getattr(error, "variant", None) or details.get("variant") or variant
    return BookPipelineError(
        package=package,
        code=code,
        message=str(error),
        stage=stage,
        chapter_id=chapter_id if isinstance(chapter_id, str) else None,
        source_number=source_number if isinstance(source_number, int) else None,
        variant=error_variant if isinstance(error_variant, str) else None,
        details=details,
    )


def _event_value(event: object, name: str) -> Any:
    value = getattr(event, name, None)
    if value is not None:
        return value
    if isinstance(event, Mapping):
        return event.get(name)
    return None


def _progress_adapter(
    stage: str,
    on_progress: ProgressHandler | None,
    *,
    variant: str | None = None,
) -> Callable[[object], None] | None:
    if on_progress is None:
        return None

    def emit(raw: object) -> None:
        raw_details = _event_value(raw, "details")
        details = dict(raw_details) if isinstance(raw_details, Mapping) else {}
        raw_kind = _event_value(raw, "kind") or _event_value(raw, "status") or "progress"
        raw_phase = _event_value(raw, "phase") or _event_value(raw, "stage") or stage
        event = BookPipelineEvent(
            stage=stage,
            kind=str(raw_kind),
            phase=str(raw_phase),
            completed=_event_value(raw, "completed")
            if isinstance(_event_value(raw, "completed"), int)
            else _event_value(raw, "completed_chapters")
            if isinstance(_event_value(raw, "completed_chapters"), int)
            else None,
            total=_event_value(raw, "total")
            if isinstance(_event_value(raw, "total"), int)
            else _event_value(raw, "requested_chapters")
            if isinstance(_event_value(raw, "requested_chapters"), int)
            else None,
            chapter_id=(_event_value(raw, "chapter_id") or _event_value(raw, "id") or None),
            source_number=(
                _event_value(raw, "source_number")
                if isinstance(_event_value(raw, "source_number"), int)
                else None
            ),
            variant=variant
            or (
                _event_value(raw, "variant")
                if isinstance(_event_value(raw, "variant"), str)
                else None
            ),
            details=details,
        )
        try:
            on_progress(event)
        except Exception as error:
            raise BookPipelineError(
                package="readio",
                code="progress.callback_failed",
                message=str(error),
                stage=stage,
                variant=variant,
            ) from error

    return emit


def _emit_stage(
    handler: ProgressHandler | None,
    *,
    stage: str,
    kind: str,
    variant: str | None = None,
) -> None:
    if handler is None:
        return
    try:
        handler(BookPipelineEvent(stage=stage, kind=kind, phase=stage, variant=variant))
    except Exception as error:
        raise BookPipelineError(
            package="readio",
            code="progress.callback_failed",
            message=str(error),
            stage=stage,
            variant=variant,
        ) from error


def _run_stage(
    operation: Callable[[], Any],
    *,
    package: str,
    stage: str,
    on_progress: ProgressHandler | None,
    variant: str | None = None,
) -> Any:
    _emit_stage(on_progress, stage=stage, kind="stage.started", variant=variant)
    try:
        result = operation()
    except BookPipelineError:
        raise
    except Exception as error:
        raise _error_from_exception(error, package=package, stage=stage, variant=variant) from error
    _emit_stage(on_progress, stage=stage, kind="stage.completed", variant=variant)
    return result


def _as_workspace(native: SSMDConvertWorkspace) -> BookWorkspace:
    root = native.path.expanduser().resolve()
    return BookWorkspace(
        root=root,
        manifest=root / "manifest.json",
        chapters_dir=root / "chapters",
        utterplan_dir=root / "utterplan",
        renderplan_dir=root / "renderplan",
        voicerender_dir=root / "voicerender",
        audiocompose_dir=root / "audiocompose",
        audioexport_dir=root / "audioexport",
        native=native,
    )


def open_book_workspace(path: str | Path) -> BookWorkspace:
    """Validate and open an existing .ssmdbook without attaching or writing state."""
    candidate = Path(path).expanduser()
    try:
        root = candidate.resolve(strict=True)
        if not root.is_dir() or root.suffix.casefold() != ".ssmdbook":
            raise ValueError("book workspace must be an existing .ssmdbook directory")
        native = _ssmdconvert_load_book_workspace(root)
    except BookPipelineError:
        raise
    except Exception as error:
        raise _error_from_exception(error, package="ssmdconvert", stage="open") from error
    if (root / ".readio").exists() or (root / ".readio").is_symlink():
        warnings.warn(
            "Legacy .readio state detected; it is no longer used by the native book pipeline.",
            UserWarning,
            stacklevel=2,
        )
    return _as_workspace(native)


def _compose_report_summary(path: Path) -> dict[str, int]:
    """Read only the shallow counts Readio presents from AudioCompose's report."""
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        with path.open("rb") as stream:
            report = tomllib.load(stream)
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(report, Mapping):
        return {}
    nested = report.get("summary")
    summary = nested if isinstance(nested, Mapping) else report
    counts: dict[str, int] = {}
    for name in ("requested", "written", "reused", "failed"):
        value = summary.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            counts[name] = value
    if "written" in counts and "reused" in counts:
        counts["succeeded"] = counts["written"] + counts["reused"]
    return counts


def inspect_book_status(path: str | Path, *, variant: str | None = None) -> dict[str, Any]:
    """Inspect stage-owned artifact paths without creating or mutating workspace state."""
    workspace = open_book_workspace(path)
    if variant is not None and (
        not variant
        or variant in {".", ".."}
        or "/" in variant
        or "\\" in variant
        or "\x00" in variant
    ):
        raise BookPipelineError(
            package="readio",
            code="request.variant_invalid",
            message="variant must be a single directory name",
            stage="status",
            variant=variant,
        )

    root = workspace.root
    chapter_count = len(workspace.native.book.chapters)
    package_roots = {
        "render-plan": workspace.renderplan_dir,
        "synth": workspace.voicerender_dir,
        "compose": workspace.audiocompose_dir,
        "export": workspace.audioexport_dir,
    }
    discovered = {
        child.name
        for package_root in package_roots.values()
        if package_root.is_dir()
        for child in package_root.iterdir()
        if child.is_dir() and not child.is_symlink()
    }
    variants = sorted(discovered | ({variant} if variant is not None else set()))

    compile_report = workspace.utterplan_dir / "compile-report.toml"
    compile_plans = sorted(workspace.utterplan_dir.glob("*.utterplan.toml"))
    variant_status: dict[str, dict[str, Any]] = {}
    for variant_id in variants:
        renderplan_root = workspace.renderplan_dir / variant_id
        synth_root = workspace.voicerender_dir / variant_id
        compose_root = workspace.audiocompose_dir / variant_id
        export_root = workspace.audioexport_dir / variant_id
        renderplan_set = renderplan_root / "renderplan-set.toml"
        synth_report = synth_root / "voicerender-report.toml"
        compose_report = compose_root / "compose-report.toml"
        compose_summary = _compose_report_summary(compose_report)
        export_sidecars = sorted(export_root.glob("*.audioexport.json"))
        export_outputs = [
            {
                "path": sidecar.with_name(
                    sidecar.name.removesuffix(".audioexport.json")
                ).as_posix(),
                "present": sidecar.with_name(
                    sidecar.name.removesuffix(".audioexport.json")
                ).is_file()
                and not sidecar.with_name(
                    sidecar.name.removesuffix(".audioexport.json")
                ).is_symlink(),
            }
            for sidecar in export_sidecars
        ]
        variant_status[variant_id] = {
            "render_plan": {
                "artifact": renderplan_set.as_posix(),
                "present": renderplan_set.is_file(),
                "plan_count": len(list(renderplan_root.glob("*.renderplan.toml"))),
            },
            "synth": {
                "artifact": synth_report.as_posix(),
                "present": synth_report.is_file(),
                "audio_count": len(list(synth_root.rglob("*.wav"))) if synth_root.is_dir() else 0,
            },
            "compose": {
                "artifact": compose_report.as_posix(),
                "present": compose_report.is_file(),
                "audio_count": len(list(compose_root.glob("*.wav"))),
                **compose_summary,
            },
            "export": {
                "artifacts": [sidecar.as_posix() for sidecar in export_sidecars],
                "outputs": export_outputs,
                "present": bool(export_sidecars)
                and all(item["present"] for item in export_outputs),
            },
        }

    return {
        "workspace": root.as_posix(),
        "source": {
            "manifest": workspace.manifest.as_posix(),
            "manifest_present": workspace.manifest.is_file(),
            "chapters_directory": workspace.chapters_dir.as_posix(),
            "chapters_directory_present": workspace.chapters_dir.is_dir(),
            "chapter_count": chapter_count,
        },
        "dirty": workspace.dirty,
        "legacy_readio_state_detected": (root / ".readio").exists()
        or (root / ".readio").is_symlink(),
        "chapter_count": chapter_count,
        "stages": {
            "compile": {
                "artifact": compile_report.as_posix(),
                "present": compile_report.is_file(),
                "plan_count": len(compile_plans),
            },
            "variants": variant_status,
        },
    }


def _default_workspace_path(source: Path) -> Path:
    name = source.name
    if name.casefold().endswith(".ssmdbook.zip"):
        name = name[: -len(".ssmdbook.zip")]
    else:
        name = source.stem
    return source.parent / f"{name}.ssmdbook"


def _single_document_book(source: Path, *, language: str | None) -> SSMDConvertBook:
    result = _ssmdconvert_convert_document(
        source,
        options=DocumentConversionOptions(language=language),
    )
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    title = result.document.metadata.get("title") or source.stem
    chapter = SSMDConvertBookChapter(
        id="document-0001",
        source_number=1,
        title=str(title),
        ssmd=result.ssmd,
    )
    return SSMDConvertBook(
        source=result.document.source,
        metadata=result.document.metadata,
        chapters=(chapter,),
        source_sha256=digest,
        source_chapter_count=1,
    )


def _selected_chapter_numbers(
    selector: str, available_numbers: tuple[int, ...], source: Path
) -> set[int]:
    try:
        return set(parse_chapter_selection(selector, available_numbers=available_numbers))
    except ValueError as error:
        raise BookPipelineError(
            package="readio",
            code="request.chapter_selection_invalid",
            message=str(error),
            stage="convert",
            details={"selector": selector, "source_path": source.as_posix()},
        ) from error


def convert_book_source(
    source: str | Path,
    output: str | Path | None = None,
    *,
    chapters: str = "all",
    language: str | None = None,
    on_progress: ProgressHandler | None = None,
) -> BookWorkspace:
    """Convert a source to a native workspace, or validate an existing workspace."""
    source_path = Path(source).expanduser()
    try:
        source_path = source_path.resolve(strict=True)
    except Exception as error:
        raise _error_from_exception(error, package="ssmdconvert", stage="convert") from error
    if not isinstance(chapters, str) or not chapters.strip():
        raise BookPipelineError(
            package="readio",
            code="request.chapter_selection_invalid",
            message="chapters must be a non-empty selector string",
            stage="convert",
        )
    if source_path.is_dir() and source_path.suffix.casefold() == ".ssmdbook":
        return open_book_workspace(source_path)

    destination = (
        Path(output).expanduser() if output is not None else _default_workspace_path(source_path)
    )
    stage_progress = _progress_adapter("convert", on_progress)

    def convert_and_write() -> Path:
        lower_name = source_path.name.casefold()
        if lower_name.endswith(".ssmdbook.zip"):
            book = _ssmdconvert_load_book_bundle(source_path)
            selected = _selected_chapter_numbers(
                chapters,
                tuple(chapter.source_number for chapter in book.chapters),
                source_path,
            )
            book = replace(
                book,
                chapters=tuple(
                    chapter for chapter in book.chapters if chapter.source_number in selected
                ),
            )
        elif source_path.suffix.casefold() == ".epub":
            conversion = _ssmdconvert_convert_book(
                source_path,
                options=BookConversionOptions(chapters=chapters, language=language),
                on_progress=stage_progress,
            )
            book = conversion.book
        else:
            if chapters.strip().casefold() not in {"all", "1"}:
                _selected_chapter_numbers(chapters, (1,), source_path)
            book = _single_document_book(source_path, language=language)
        return _ssmdconvert_write_book_bundle(book, destination, format="directory")

    written = _run_stage(
        convert_and_write,
        package="ssmdconvert",
        stage="convert",
        on_progress=on_progress,
    )
    return open_book_workspace(written)


def _workspace_root(workspace: BookWorkspace | str | Path) -> Path:
    if isinstance(workspace, BookWorkspace):
        return workspace.root
    return open_book_workspace(workspace).root


def compile_book(
    workspace: BookWorkspace | str | Path,
    *,
    chapters: str = "all",
    force: bool = False,
    fail_fast: bool = False,
    on_progress: ProgressHandler | None = None,
) -> object:
    root = _workspace_root(workspace)
    return _run_stage(
        lambda: _utterplan_compile_book(
            root,
            chapters=chapters,
            force=force,
            fail_fast=fail_fast,
            on_progress=_progress_adapter("compile", on_progress),
        ),
        package="utterplan",
        stage="compile",
        on_progress=on_progress,
    )


def render_book_plan(
    workspace: BookWorkspace | str | Path,
    *,
    profile: str | Path,
    chapters: str = "all",
    variant: str | None = None,
    force: bool = False,
    fail_fast: bool = False,
    on_progress: ProgressHandler | None = None,
) -> object:
    root = _workspace_root(workspace)
    return _run_stage(
        lambda: _utterplan_render_book(
            root,
            profile=profile,
            chapters=chapters,
            variant=variant,
            force=force,
            fail_fast=fail_fast,
            on_progress=_progress_adapter("render-plan", on_progress, variant=variant),
        ),
        package="utterplan",
        stage="render-plan",
        on_progress=on_progress,
        variant=variant,
    )


def create_render_profile(
    path: str | Path,
    *,
    engine: str,
    target_id: str,
    language: str,
    role: str = "narrator",
    voice: str | None = None,
    speaker: str | int | None = None,
    voice_source: Mapping[str, Any] | None = None,
    options: Mapping[str, Any] | None = None,
    offline: bool = False,
    refresh: bool = False,
) -> Path:
    """Create a normal VoiceRender/RenderPlan profile at the requested path."""
    target = Path(path).expanduser()
    try:
        profile = _voicerender_create_render_profile(
            engine=engine,
            target_id=target_id,
            language=language,
            role=role,
            voice=voice,
            speaker=speaker,
            voice_source=voice_source,
            options=options,
            offline=offline,
            refresh=refresh,
        )
        return Path(_voicerender_save_render_profile(profile, target))
    except Exception as error:
        raise _error_from_exception(error, package="voicerender", stage="profile") from error


def synthesize_book(
    workspace: BookWorkspace | str | Path,
    *,
    variant: str,
    chapters: str = "all",
    force: bool = False,
    fail_fast: bool = False,
    emphasis_policy: str = "defer-to-compose",
    on_progress: ProgressHandler | None = None,
) -> object:
    root = _workspace_root(workspace)
    return _run_stage(
        lambda: _voicerender_render_book(
            root,
            variant=variant,
            chapters=chapters,
            force=force,
            fail_fast=fail_fast,
            emphasis_policy=emphasis_policy,
        ),
        package="voicerender",
        stage="synth",
        on_progress=on_progress,
        variant=variant,
    )


def compose_book(
    workspace: BookWorkspace | str | Path,
    *,
    variant: str,
    chapters: str = "all",
    force: bool = False,
    fail_fast: bool = False,
    on_progress: ProgressHandler | None = None,
) -> object:
    root = _workspace_root(workspace)
    return _run_stage(
        lambda: _audiocompose_compose_book(
            root,
            variant=variant,
            chapters=chapters,
            force=force,
            fail_fast=fail_fast,
            on_progress=_progress_adapter("compose", on_progress, variant=variant),
        ),
        package="audiocompose",
        stage="compose",
        on_progress=on_progress,
        variant=variant,
    )


def export_book(
    workspace: BookWorkspace | str | Path,
    *,
    variant: str,
    chapters: str = "all",
    force: bool = False,
    output: str | Path | None = None,
    profile: str | Path | None = None,
    metadata: Mapping[str, Any] | None = None,
    cover: str | Path | None = None,
    bitrate: str | int | None = None,
    on_progress: ProgressHandler | None = None,
) -> object:
    root = _workspace_root(workspace)
    return _run_stage(
        lambda: _audioexport_export_book(
            root,
            variant=variant,
            chapters=chapters,
            force=force,
            output=output,
            profile=profile,
            metadata=metadata,
            cover=cover,
            bitrate=bitrate,
        ),
        package="audioexport",
        stage="export",
        on_progress=on_progress,
        variant=variant,
    )


def build_book(
    request: BookBuildRequest, *, on_progress: ProgressHandler | None = None
) -> BookBuildResult:
    """Run the selected native stages and return their in-memory results."""
    workspace = open_book_workspace(request.workspace)
    compile_result = None
    renderplan_result = None
    voicerender_result = None
    compose_result = None
    export_result = None
    variant = request.variant

    if request.compile:
        compile_result = compile_book(
            workspace,
            chapters=request.chapters,
            force=request.force,
            fail_fast=request.fail_fast,
            on_progress=on_progress,
        )
    if request.project_renderplans:
        renderplan_result = render_book_plan(
            workspace,
            profile=request.profile,
            chapters=request.chapters,
            variant=variant,
            force=request.force,
            fail_fast=request.fail_fast,
            on_progress=on_progress,
        )
        produced_variant = getattr(renderplan_result, "variant", None)
        if not isinstance(produced_variant, str) or not produced_variant:
            raise BookPipelineError(
                package="utterplan",
                code="renderplan.variant_missing",
                message="UtterPlan render_book did not return a variant ID",
                stage="render-plan",
            )
        variant = produced_variant
        if request.stop_after == "render-plan":
            return BookBuildResult(
                workspace.root, request.chapters, variant, compile_result, renderplan_result
            )

    needs_variant = request.synthesize or request.compose or request.export
    if needs_variant and not variant:
        raise BookPipelineError(
            package="readio",
            code="request.variant_required",
            message="a variant is required when render-plan projection is skipped",
            stage="build",
        )
    if request.synthesize:
        voicerender_result = synthesize_book(
            workspace,
            variant=variant or "",
            chapters=request.chapters,
            force=request.force,
            fail_fast=request.fail_fast,
            emphasis_policy=request.emphasis_policy,
            on_progress=on_progress,
        )
        if request.stop_after == "synth":
            return BookBuildResult(
                workspace.root,
                request.chapters,
                variant or "",
                compile_result,
                renderplan_result,
                voicerender_result,
            )
    if request.compose:
        compose_result = compose_book(
            workspace,
            variant=variant or "",
            chapters=request.chapters,
            force=request.force,
            fail_fast=request.fail_fast,
            on_progress=on_progress,
        )
        if request.stop_after == "compose":
            return BookBuildResult(
                workspace.root,
                request.chapters,
                variant or "",
                compile_result,
                renderplan_result,
                voicerender_result,
                compose_result,
            )
    if request.export:
        export_result = export_book(
            workspace,
            variant=variant or "",
            chapters=request.chapters,
            force=request.force,
            output=request.output,
            profile=request.export_profile,
            metadata=request.metadata or None,
            cover=request.cover,
            bitrate=request.bitrate,
            on_progress=on_progress,
        )
    return BookBuildResult(
        workspace=workspace.root,
        chapters=request.chapters,
        variant=variant or "",
        compile_result=compile_result,
        renderplan_result=renderplan_result,
        voicerender_result=voicerender_result,
        compose_result=compose_result,
        export_result=export_result,
    )


__all__ = [
    "BookBuildRequest",
    "BookBuildResult",
    "BookPipelineError",
    "BookPipelineEvent",
    "BookWorkspace",
    "build_book",
    "compile_book",
    "compose_book",
    "convert_book_source",
    "create_render_profile",
    "export_book",
    "inspect_book_status",
    "open_book_workspace",
    "render_book_plan",
    "synthesize_book",
]
