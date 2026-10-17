"""Single-document staged orchestration over Readio's specialist packages."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

import audiocompose  # type: ignore[import-not-found]
import audioexport  # type: ignore[import-not-found]
import renderplan  # type: ignore[import-not-found]
import ssmdconvert  # type: ignore[import-not-found]
import utterplan  # type: ignore[import-not-found]
import utterplan.toml_codec  # type: ignore[import-not-found]
import voicerender  # type: ignore[import-not-found]

from ._outputs import save_specialist_file

DocumentStopAfter = Literal["convert", "plan", "render-plan", "synth", "compose", "export"]
ProgressHandler = Callable[["ProgressEvent"], None]


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    stage: str
    kind: str
    current: int | None = None
    total: int | None = None
    message: str | None = None
    native: object | None = None


@dataclass(frozen=True, slots=True)
class DocumentRenderRequest:
    source: Path
    profile: Path
    output: Path | None = None
    artifacts: Path | None = None
    keep_artifacts: bool = False
    clean_artifacts: bool = False
    stop_after: DocumentStopAfter | None = None

    conversion_options: ssmdconvert.DocumentConversionOptions | None = None
    planner_config: utterplan.PlannerConfig | None = None

    cache_dir: Path | None = None
    speech_selector: str = "all"
    render_emphasis_policy: str = "defer-to-compose"
    reset_stale_output: bool = False

    compose_output_policy: audiocompose.OutputPolicy | None = None
    compose_emphasis_policy: str = "segment_fallback"
    media_root: Path | None = None
    save_audiojob: Path | None = None

    export_format: str | None = None
    export_bitrate: str | int | None = None
    export_sample_rate: int | None = None
    export_channels: int | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    cover: Path | None = None
    ffmpeg: Path | None = None
    ffprobe: Path | None = None

    force: bool = False

    def __post_init__(self) -> None:
        for name in (
            "source",
            "profile",
            "output",
            "artifacts",
            "cache_dir",
            "media_root",
            "save_audiojob",
            "cover",
            "ffmpeg",
            "ffprobe",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, Path(value).expanduser())
        object.__setattr__(self, "metadata", dict(self.metadata))
        if self.stop_after not in {
            None,
            "convert",
            "plan",
            "render-plan",
            "synth",
            "compose",
            "export",
        }:
            raise ValueError("invalid stop_after stage")
        if not self.speech_selector.strip():
            raise ValueError("speech_selector must be a non-empty selector")


@dataclass(frozen=True, slots=True)
class DocumentRenderResult:
    artifacts_dir: Path
    ssmd_path: Path | None = None
    utterplan_path: Path | None = None
    renderplan_path: Path | None = None
    preflight_path: Path | None = None
    render_dir: Path | None = None
    composed_wav: Path | None = None
    output: Path | None = None

    conversion_result: object | None = None
    compile_result: object | None = None
    renderplan: object | None = None
    preflight_report: object | None = None
    render_result: object | None = None
    compose_result: object | None = None
    export_result: object | None = None


_STAGE_FILES = (
    ".ssmd.md",
    ".utterplan.toml",
    ".renderplan.toml",
    ".preflight.toml",
    ".composed.wav",
    ".audiojob.toml",
)
_RENDER_FILE = re.compile(r"(?:render-manifest\.toml|[0-9]{6}\.wav)\Z")


def _artifact_directory(source: Path) -> Path:
    return source.with_name(f"{source.stem}.render")


def _remove_expected_artifacts(path: Path, source: Path) -> None:
    expected = _artifact_directory(source).resolve()
    if path.resolve() != expected:
        raise ValueError(
            "--clean-artifacts is limited to the expected <source-stem>.render directory"
        )
    if not path.exists():
        return
    if path.is_symlink() or not path.is_dir():
        raise ValueError("refusing to clean a non-directory or symlink artifact path")
    allowed = {f"{source.stem}{suffix}" for suffix in _STAGE_FILES} | {"renders"}
    children = tuple(path.iterdir())
    unknown = [child.name for child in children if child.name not in allowed]
    if unknown:
        raise ValueError(
            f"refusing to clean unexpected artifact entries: {', '.join(sorted(unknown))}"
        )
    for child in children:
        if child.name == "renders":
            if child.is_symlink() or not child.is_dir():
                raise ValueError("refusing to clean an unexpected renders path")
            render_files = tuple(child.iterdir())
            if any(
                item.is_symlink() or not item.is_file() or not _RENDER_FILE.fullmatch(item.name)
                for item in render_files
            ):
                raise ValueError("refusing to clean unexpected entries in renders/")
            for item in render_files:
                item.unlink()
            child.rmdir()
        else:
            if child.is_symlink() or not child.is_file():
                raise ValueError(f"refusing to clean unexpected artifact: {child}")
            child.unlink()
    path.rmdir()


@contextmanager
def _artifact_directory_for(request: DocumentRenderRequest):
    source = request.source
    if request.artifacts is not None:
        path = request.artifacts
        if request.clean_artifacts:
            _remove_expected_artifacts(path, source)
        if path.exists() and (path.is_symlink() or not path.is_dir()):
            raise ValueError(f"artifact path must be a directory and not a symlink: {path}")
        path.mkdir(parents=True, exist_ok=True)
        yield path
        return
    if request.keep_artifacts:
        path = _artifact_directory(source)
        if request.clean_artifacts:
            _remove_expected_artifacts(path, source)
        if path.exists() and (path.is_symlink() or not path.is_dir()):
            raise ValueError(f"artifact path must be a directory and not a symlink: {path}")
        path.mkdir(parents=True, exist_ok=True)
        yield path
        return
    if request.clean_artifacts:
        raise ValueError("--clean-artifacts requires --keep-artifacts or --artifacts DIR")
    with TemporaryDirectory(prefix="readio-render-") as temporary:
        yield Path(temporary)


def _emit(
    handler: ProgressHandler | None, stage: str, kind: str, native: object | None = None
) -> None:
    if handler is None:
        return
    handler(
        ProgressEvent(
            stage=stage,
            kind=kind,
            current=getattr(native, "current", None) if native is not None else None,
            total=getattr(native, "total", None) if native is not None else None,
            message=getattr(native, "message", None) if native is not None else None,
            native=native,
        )
    )


def _run_stage(
    handler: ProgressHandler | None,
    stage: str,
    operation: Callable[[], Any],
) -> Any:
    _emit(handler, stage, "stage.started")
    result = operation()
    _emit(handler, stage, "stage.completed")
    return result


def _speech_numbers(selector: str) -> tuple[int, ...] | None:
    value = selector.strip().casefold()
    if value == "all":
        return None
    numbers: set[int] = set()
    for part in value.split(","):
        token = part.strip()
        if not token:
            raise ValueError(f"invalid speech selector: {selector!r}")
        if "-" in token:
            start_text, separator, end_text = token.partition("-")
            if not separator or not start_text.isdigit() or not end_text.isdigit():
                raise ValueError(f"invalid speech selector: {selector!r}")
            start, end = int(start_text), int(end_text)
            if start <= 0 or end < start:
                raise ValueError(f"invalid speech selector range: {token!r}")
            numbers.update(range(start, end + 1))
        else:
            if not token.isdigit() or int(token) <= 0:
                raise ValueError(f"invalid speech selector: {selector!r}")
            numbers.add(int(token))
    return tuple(sorted(numbers))


def _write_ssmd(path: Path, ssmd: str, *, force: bool) -> None:
    save_specialist_file(
        path, lambda destination: destination.write_text(ssmd, encoding="utf-8"), force=force
    )


def _save_utterplan(plan: object, path: Path, *, force: bool) -> None:
    save_specialist_file(
        path, lambda destination: utterplan.toml_codec.dump(plan, destination), force=force
    )


def _save_renderplan(plan: object, path: Path, *, force: bool) -> None:
    save_specialist_file(
        path, lambda destination: renderplan.save_plan(plan, destination), force=force
    )


def _save_preflight(report: object, path: Path, *, force: bool) -> None:
    save_specialist_file(
        path, lambda destination: renderplan.save_preflight(report, destination), force=force
    )


def _remove_existing_file(path: Path, *, force: bool) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"refusing to replace non-file output: {path}")
    if not force:
        raise FileExistsError(f"output already exists: {path}")
    path.unlink()


def _validate_render_directory(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError(f"render output must be a real directory, not a file or symlink: {path}")


def _output_format(request: DocumentRenderRequest, output: Path) -> str:
    if request.export_format:
        return request.export_format.casefold().lstrip(".")
    suffix = output.suffix.casefold().lstrip(".")
    return suffix if suffix in audioexport.FORMATS else "m4a"


def render_document(
    request: DocumentRenderRequest,
    *,
    on_progress: ProgressHandler | None = None,
) -> DocumentRenderResult:
    """Run the public specialist APIs in order and retain their native result objects."""
    source = request.source
    stem = source.stem
    with _artifact_directory_for(request) as artifacts_dir:
        ssmd_path = artifacts_dir / f"{stem}.ssmd.md"
        utterplan_path = artifacts_dir / f"{stem}.utterplan.toml"
        renderplan_path = artifacts_dir / f"{stem}.renderplan.toml"
        preflight_path = artifacts_dir / f"{stem}.preflight.toml"
        render_dir = artifacts_dir / "renders"
        composed_wav = artifacts_dir / f"{stem}.composed.wav"

        conversion_options = request.conversion_options or ssmdconvert.DocumentConversionOptions()
        converted = _run_stage(
            on_progress,
            "convert",
            lambda: ssmdconvert.convert_document(source, options=conversion_options),
        )
        _write_ssmd(ssmd_path, converted.ssmd, force=request.force)
        result = DocumentRenderResult(
            artifacts_dir=artifacts_dir,
            ssmd_path=ssmd_path,
            conversion_result=converted,
        )
        if request.stop_after == "convert":
            return result

        default_language = conversion_options.language or "en-US"
        planner_config = request.planner_config or utterplan.PlannerConfig(
            language=default_language,
            document_format="ssmd",
        )
        compiled = _run_stage(
            on_progress,
            "plan",
            lambda: utterplan.compile_document(
                converted.ssmd,
                input_format="ssmd",
                config=planner_config,
                on_progress=lambda event: _emit(on_progress, "plan", "progress", event),
            ),
        )
        _save_utterplan(compiled.plan, utterplan_path, force=request.force)
        result = DocumentRenderResult(
            artifacts_dir=artifacts_dir,
            ssmd_path=ssmd_path,
            utterplan_path=utterplan_path,
            conversion_result=converted,
            compile_result=compiled,
        )
        if request.stop_after == "plan":
            return result

        profile = voicerender.load_render_profile(request.profile)
        render_plan = _run_stage(
            on_progress,
            "render-plan",
            lambda: utterplan.build_render_plan(compiled.plan, profile),
        )
        _save_renderplan(render_plan, renderplan_path, force=request.force)
        result = DocumentRenderResult(
            artifacts_dir=artifacts_dir,
            ssmd_path=ssmd_path,
            utterplan_path=utterplan_path,
            renderplan_path=renderplan_path,
            conversion_result=converted,
            compile_result=compiled,
            renderplan=render_plan,
        )
        if request.stop_after == "render-plan":
            return result

        def synthesize() -> tuple[object, object]:
            _validate_render_directory(render_dir)
            preflight = voicerender.preflight_render_plan(
                render_plan,
                render_dir,
                emphasis_policy=request.render_emphasis_policy,
                on_event=lambda event: _emit(on_progress, "synth", "progress", event),
            )
            _save_preflight(preflight, preflight_path, force=request.force)
            rendered = voicerender.render_renderplan(
                render_plan,
                preflight=preflight,
                output_dir=render_dir,
                cache_dir=request.cache_dir,
                force=request.force,
                reset_stale_output=request.reset_stale_output,
                speech_numbers=_speech_numbers(request.speech_selector),
                emphasis_policy=request.render_emphasis_policy,
                on_event=lambda event: _emit(on_progress, "synth", "progress", event),
            )
            return preflight, rendered

        preflight, render_result = _run_stage(on_progress, "synth", synthesize)
        result = DocumentRenderResult(
            artifacts_dir=artifacts_dir,
            ssmd_path=ssmd_path,
            utterplan_path=utterplan_path,
            renderplan_path=renderplan_path,
            preflight_path=preflight_path,
            render_dir=render_dir,
            conversion_result=converted,
            compile_result=compiled,
            renderplan=render_plan,
            preflight_report=preflight,
            render_result=render_result,
        )
        if request.stop_after == "synth":
            return result

        compose_policy = request.compose_output_policy
        if request.export_sample_rate is not None or request.export_channels is not None:
            base_policy = compose_policy or audiocompose.OutputPolicy()
            compose_policy = audiocompose.OutputPolicy(
                sample_rate=(
                    request.export_sample_rate
                    if request.export_sample_rate is not None
                    else base_policy.sample_rate
                ),
                channels=(
                    request.export_channels
                    if request.export_channels is not None
                    else base_policy.channels
                ),
                loudness=base_policy.loudness,
                clip_policy=base_policy.clip_policy,
            )
        _remove_existing_file(composed_wav, force=request.force)
        compose_result = _run_stage(
            on_progress,
            "compose",
            lambda: audiocompose.compose_renderplan(
                render_plan,
                render_dir=render_dir,
                output=composed_wav,
                media_root=request.media_root,
                output_policy=compose_policy,
                save_audiojob=request.save_audiojob,
                emphasis_policy=request.compose_emphasis_policy,
                on_progress=lambda event: _emit(on_progress, "compose", "progress", event),
            ),
        )
        result = DocumentRenderResult(
            artifacts_dir=artifacts_dir,
            ssmd_path=ssmd_path,
            utterplan_path=utterplan_path,
            renderplan_path=renderplan_path,
            preflight_path=preflight_path,
            render_dir=render_dir,
            composed_wav=composed_wav,
            conversion_result=converted,
            compile_result=compiled,
            renderplan=render_plan,
            preflight_report=preflight,
            render_result=render_result,
            compose_result=compose_result,
        )
        if request.stop_after == "compose":
            return result

        output = request.output
        format_source = output if output is not None else source.with_suffix(".m4a")
        export_format = _output_format(request, format_source)
        if output is None:
            output = source.with_suffix(f".{export_format}")
        export_result = _run_stage(
            on_progress,
            "export",
            lambda: audioexport.encode(
                composed_wav,
                output,
                format=export_format,
                bitrate=request.export_bitrate,
                metadata=dict(request.metadata) or None,
                cover=request.cover,
                force=request.force,
                ffmpeg=request.ffmpeg,
                ffprobe=request.ffprobe,
            ),
        )
        return DocumentRenderResult(
            artifacts_dir=artifacts_dir,
            ssmd_path=ssmd_path,
            utterplan_path=utterplan_path,
            renderplan_path=renderplan_path,
            preflight_path=preflight_path,
            render_dir=render_dir,
            composed_wav=composed_wav,
            output=Path(output),
            conversion_result=converted,
            compile_result=compiled,
            renderplan=render_plan,
            preflight_report=preflight,
            render_result=render_result,
            compose_result=compose_result,
            export_result=export_result,
        )


__all__ = [
    "DocumentRenderRequest",
    "DocumentRenderResult",
    "DocumentStopAfter",
    "ProgressEvent",
    "ProgressHandler",
    "render_document",
]
