"""Thin orchestration for specialist-owned .ssmdbook workspaces."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import audiocompose  # type: ignore[import-not-found]
import audioexport  # type: ignore[import-not-found]
import ssmdconvert  # type: ignore[import-not-found]
import utterplan  # type: ignore[import-not-found]
import voicerender  # type: ignore[import-not-found]

StopAfter = Literal["compile", "render-plan", "synth", "compose", "export"]


@dataclass(frozen=True, slots=True)
class BookProgressEvent:
    stage: str
    kind: str


ProgressHandler = Callable[[BookProgressEvent], None]


@dataclass(frozen=True, slots=True)
class BookBuildRequest:
    workspace: Path
    profile: Path | None = None
    chapters: str = "all"
    compile: bool = True
    render_plan: bool = True
    synthesize: bool = True
    compose: bool = True
    export: bool = True
    force: bool = False
    fail_fast: bool = False
    variant: str | None = None
    stop_after: StopAfter | None = None
    output: Path | None = None
    export_profile: Path | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    cover: Path | None = None
    bitrate: str | int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", Path(self.workspace))
        for name in ("profile", "output", "export_profile", "cover"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, Path(value))
        object.__setattr__(self, "metadata", dict(self.metadata))
        if not self.chapters.strip():
            raise ValueError("chapters must be a non-empty selector")
        if self.stop_after not in {None, "compile", "render-plan", "synth", "compose", "export"}:
            raise ValueError("invalid stop_after stage")


@dataclass(frozen=True, slots=True)
class BookBuildResult:
    workspace: Path
    variant: str | None
    compile_result: object | None = None
    renderplan_result: object | None = None
    voicerender_result: object | None = None
    compose_result: object | None = None
    export_result: object | None = None


def open_book(workspace: str | Path) -> ssmdconvert.BookWorkspace:
    """Open a specialist-owned workspace without initializing or modifying it."""
    return ssmdconvert.load_book_workspace(workspace)


def convert_book(
    source: str | Path,
    output: str | Path | None = None,
    *,
    chapters: str = "all",
    language: str | None = None,
    on_progress: Callable[[object], None] | None = None,
) -> ssmdconvert.BookWorkspace:
    """Convert an EPUB and write the resulting SSMDConvert-owned workspace."""
    source_path = Path(source).expanduser()
    output_path = (
        Path(output).expanduser() if output is not None else source_path.with_suffix(".ssmdbook")
    )
    converted = ssmdconvert.convert_book(
        source_path,
        options=ssmdconvert.BookConversionOptions(chapters=chapters, language=language),
        on_progress=on_progress,
    )
    written = ssmdconvert.write_book_bundle(converted.book, output_path, format="directory")
    return ssmdconvert.load_book_workspace(written)


def _run_stage(
    stage: str,
    operation: Callable[[], Any],
    on_progress: ProgressHandler | None,
) -> Any:
    if on_progress is not None:
        on_progress(BookProgressEvent(stage, "stage.started"))
    result = operation()
    if on_progress is not None:
        on_progress(BookProgressEvent(stage, "stage.completed"))
    return result


def _build_result(
    workspace: Path,
    variant: str | None,
    compile_result: object | None,
    renderplan_result: object | None,
    voicerender_result: object | None,
    compose_result: object | None,
    export_result: object | None,
) -> BookBuildResult:
    return BookBuildResult(
        workspace=workspace,
        variant=variant,
        compile_result=compile_result,
        renderplan_result=renderplan_result,
        voicerender_result=voicerender_result,
        compose_result=compose_result,
        export_result=export_result,
    )


def build_book(
    request: BookBuildRequest,
    *,
    on_progress: ProgressHandler | None = None,
) -> BookBuildResult:
    """Run selected specialist stages and return their original result objects."""
    workspace = open_book(request.workspace)
    root = workspace.path
    compile_result = None
    renderplan_result = None
    voicerender_result = None
    compose_result = None
    export_result = None
    variant = request.variant

    if request.compile:
        compile_result = _run_stage(
            "compile",
            lambda: utterplan.compile_book(
                root,
                chapters=request.chapters,
                force=request.force,
                fail_fast=request.fail_fast,
            ),
            on_progress,
        )
        if request.stop_after == "compile":
            return _build_result(
                root,
                variant,
                compile_result,
                renderplan_result,
                voicerender_result,
                compose_result,
                export_result,
            )

    if request.render_plan:
        if request.profile is None:
            raise ValueError("profile is required when render-plan projection is enabled")
        renderplan_result = _run_stage(
            "render-plan",
            lambda: utterplan.render_book(
                root,
                profile=request.profile,
                chapters=request.chapters,
                variant=variant,
                force=request.force,
                fail_fast=request.fail_fast,
            ),
            on_progress,
        )
        variant = renderplan_result.variant
        if request.stop_after == "render-plan":
            return _build_result(
                root,
                variant,
                compile_result,
                renderplan_result,
                voicerender_result,
                compose_result,
                export_result,
            )

    if (request.synthesize or request.compose or request.export) and not variant:
        raise ValueError("variant is required when render-plan projection is skipped")

    if request.synthesize:
        voicerender_result = _run_stage(
            "synth",
            lambda: voicerender.render_book(
                root,
                variant=variant,
                chapters=request.chapters,
                force=request.force,
                fail_fast=request.fail_fast,
                emphasis_policy="defer-to-compose",
            ),
            on_progress,
        )
        if request.stop_after == "synth":
            return _build_result(
                root,
                variant,
                compile_result,
                renderplan_result,
                voicerender_result,
                compose_result,
                export_result,
            )

    if request.compose:
        compose_result = _run_stage(
            "compose",
            lambda: audiocompose.compose_book(
                root,
                variant=variant,
                chapters=request.chapters,
                force=request.force,
                fail_fast=request.fail_fast,
            ),
            on_progress,
        )
        if request.stop_after == "compose":
            return _build_result(
                root,
                variant,
                compile_result,
                renderplan_result,
                voicerender_result,
                compose_result,
                export_result,
            )

    if request.export:
        export_result = _run_stage(
            "export",
            lambda: audioexport.export_book(
                root,
                variant=variant,
                chapters=request.chapters,
                output=request.output,
                profile=request.export_profile,
                metadata=request.metadata or None,
                cover=request.cover,
                bitrate=request.bitrate,
                force=request.force,
            ),
            on_progress,
        )

    return _build_result(
        root,
        variant,
        compile_result,
        renderplan_result,
        voicerender_result,
        compose_result,
        export_result,
    )
