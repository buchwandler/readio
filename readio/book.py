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
    current: int | None = None
    total: int | None = None
    message: str | None = None
    native: object | None = None


ProgressHandler = Callable[[BookProgressEvent], None]


@dataclass(frozen=True, slots=True)
class BookBuildRequest:
    workspace: Path
    profile: Path | None = None
    chapters: str = "all"
    variant: str | None = None

    compile: bool = True
    render_plan: bool = True
    synthesize: bool = True
    compose: bool = True
    export: bool = True
    stop_after: StopAfter | None = None

    compile_request: utterplan.BookCompileRequest | None = None
    render_request: utterplan.BookRenderRequest | None = None

    speech: str = "all"
    renderplan_root: Path | None = None
    voicerender_output_root: Path | None = None
    cache_dir: Path | None = None
    reset_stale_output: bool = False
    render_emphasis_policy: str = "defer-to-compose"

    compose_options: audiocompose.BookComposeOptions | None = None
    compose_output_policy: audiocompose.OutputPolicy | None = None
    compose_renderplan_root: Path | None = None
    compose_voicerender_root: Path | None = None
    compose_output_root: Path | None = None
    media_root: Path | None = None
    save_audiojobs: Path | None = None
    compose_emphasis_policy: str = "segment_fallback"

    output: Path | None = None
    export_profile: Path | None = None
    export_metadata: Mapping[str, str] = field(default_factory=dict)
    cover: Path | None = None
    bitrate: str | int | None = None
    export_sample_rate: int | None = None
    export_channels: int | None = None
    ffmpeg: Path | None = None
    ffprobe: Path | None = None

    force: bool = False
    fail_fast: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", Path(self.workspace).expanduser())
        path_fields = (
            "profile",
            "renderplan_root",
            "voicerender_output_root",
            "cache_dir",
            "compose_renderplan_root",
            "compose_voicerender_root",
            "compose_output_root",
            "media_root",
            "save_audiojobs",
            "output",
            "export_profile",
            "cover",
            "ffmpeg",
            "ffprobe",
        )
        for name in path_fields:
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, Path(value).expanduser())
        object.__setattr__(self, "export_metadata", dict(self.export_metadata))
        if not self.chapters.strip():
            raise ValueError("chapters must be a non-empty selector")
        if not self.speech.strip():
            raise ValueError("speech must be a non-empty selector")
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


def _forward_progress(
    handler: ProgressHandler | None,
    stage: str,
    native: object,
) -> None:
    if handler is None:
        return
    handler(
        BookProgressEvent(
            stage=stage,
            kind=str(getattr(native, "kind", "progress")),
            current=getattr(native, "current", None),
            total=getattr(native, "total", None),
            message=getattr(native, "message", None),
            native=native,
        )
    )


def convert_book(
    source: str | Path,
    output: str | Path | None = None,
    *,
    chapters: str = "all",
    language: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    sequence_fallback_mode: str | None = None,
    speech_mode: str | None = None,
    spacy: str | None = None,
    on_progress: ProgressHandler | None = None,
) -> ssmdconvert.BookWorkspace:
    """Convert an EPUB using SSMDConvert's native options and workspace writer."""
    source_path = Path(source).expanduser()
    output_path = (
        Path(output).expanduser() if output is not None else source_path.with_suffix(".ssmdbook")
    )
    defaults = ssmdconvert.BookConversionOptions()
    speech_defaults = defaults.speech
    options = ssmdconvert.BookConversionOptions(
        chapters=chapters,
        language=language,
        metadata_overrides=metadata or {},
        sequence_fallback_mode=sequence_fallback_mode
        if sequence_fallback_mode is not None
        else defaults.sequence_fallback_mode,
        speech=ssmdconvert.SpeechOptions(
            mode=speech_mode if speech_mode is not None else speech_defaults.mode,
            spacy=spacy if spacy is not None else speech_defaults.spacy,
        ),
    )
    converted = ssmdconvert.convert_book(
        source_path,
        options=options,
        on_progress=(
            (lambda event: _forward_progress(on_progress, "convert", event))
            if on_progress is not None
            else None
        ),
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
    if variant is None and request.render_request is not None:
        variant = request.render_request.variant

    if request.compile:
        compile_request = request.compile_request or utterplan.BookCompileRequest(
            book=root,
            chapters=request.chapters,
            force=request.force,
            fail_fast=request.fail_fast,
        )
        compile_result = _run_stage(
            "compile",
            lambda: utterplan.compile_book(
                compile_request.book,
                chapters=compile_request.chapters,
                output_dir=compile_request.output_dir,
                report=compile_request.report,
                language=compile_request.language,
                planner_config=compile_request.planner_config,
                force=compile_request.force,
                fail_fast=compile_request.fail_fast,
                diagnostics=compile_request.diagnostics,
                on_progress=lambda event: _forward_progress(on_progress, "compile", event),
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
        render_request = request.render_request
        if render_request is None:
            if request.profile is None:
                raise ValueError("profile is required when render-plan projection is enabled")
            render_request = utterplan.BookRenderRequest(
                book=root,
                profile=request.profile,
                chapters=request.chapters,
                variant=variant,
                force=request.force,
                fail_fast=request.fail_fast,
            )
        profile = request.profile or render_request.profile
        render_variant = variant if variant is not None else render_request.variant
        renderplan_result = _run_stage(
            "render-plan",
            lambda: utterplan.render_book(
                render_request.book,
                profile=profile,
                chapters=render_request.chapters,
                variant=render_variant,
                utterplan_dir=render_request.utterplan_dir,
                output_root=render_request.output_root,
                report=render_request.report,
                force=render_request.force,
                fail_fast=render_request.fail_fast,
                on_progress=lambda event: _forward_progress(on_progress, "render-plan", event),
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
                speech=request.speech,
                renderplan_root=request.renderplan_root,
                output_root=request.voicerender_output_root,
                cache_dir=request.cache_dir,
                force=request.force,
                reset_stale_output=request.reset_stale_output,
                fail_fast=request.fail_fast,
                emphasis_policy=request.render_emphasis_policy,
                on_event=lambda event: _forward_progress(on_progress, "synth", event),
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
                options=request.compose_options,
                output_policy=request.compose_output_policy,
                renderplan_root=request.compose_renderplan_root,
                voicerender_root=request.compose_voicerender_root,
                output_root=request.compose_output_root,
                media_root=request.media_root,
                save_audiojobs=request.save_audiojobs,
                force=request.force,
                fail_fast=request.fail_fast,
                emphasis_policy=request.compose_emphasis_policy,
                on_progress=lambda event: _forward_progress(on_progress, "compose", event),
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
                metadata=dict(request.export_metadata) or None,
                cover=request.cover,
                bitrate=request.bitrate,
                sample_rate=request.export_sample_rate,
                channels=request.export_channels,
                force=request.force,
                ffmpeg=request.ffmpeg,
                ffprobe=request.ffprobe,
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
