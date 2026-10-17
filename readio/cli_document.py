"""CLI surface for staged single-document rendering."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import audiocompose  # type: ignore[import-not-found]
import ssmdconvert  # type: ignore[import-not-found]
import utterplan  # type: ignore[import-not-found]

from .cli_config import ConfigurationError
from .cli_control import CLIUsageError
from .document import DocumentRenderRequest, DocumentRenderResult, ProgressEvent


def add_document_command(commands: Any) -> None:
    render = commands.add_parser("render", help="Render one document through specialist stages")
    render.add_argument("source", type=Path)
    render.add_argument("--profile", type=Path, required=True)
    render.add_argument("-o", "--output", type=Path)
    render.add_argument("--config", type=Path, help="Explicit Readio settings file")
    render.add_argument("--artifacts", type=Path)
    render.add_argument("--keep-artifacts", action="store_true")
    render.add_argument("--clean-artifacts", action="store_true")
    render.add_argument(
        "--stop-after",
        choices=("convert", "plan", "render-plan", "synth", "compose", "export"),
    )
    render.add_argument("--cache-dir", type=Path)
    render.add_argument("--speech", default="all", metavar="SELECTOR")
    render.add_argument("--emphasis-policy", choices=("reject", "defer-to-compose"))
    render.add_argument("--force", action="store_true", default=None)
    render.add_argument("--reset-stale", action="store_true", default=None)

    render.add_argument("--language")
    render.add_argument("--title")
    render.add_argument("--author")
    render.add_argument("--metadata", action="append", default=[], metavar="KEY=VALUE")
    render.add_argument("--sequence-fallback-mode")

    render.add_argument("--text-preparation", choices=("auto", "ssmd", "spokenform", "identity"))
    render.add_argument("--overlap-mode", choices=("snap", "strict"))
    render.add_argument("--renderability-mode", choices=("repair", "strict"))
    render.add_argument("--spacy", choices=("auto", "on", "off"))
    render.add_argument("--spacy-model")
    render.add_argument("--spacy-model-size", choices=("sm", "md", "lg", "trf"))
    spacy_required = render.add_mutually_exclusive_group()
    spacy_required.add_argument("--require-spacy", dest="require_spacy", action="store_true")
    spacy_required.add_argument("--no-require-spacy", dest="require_spacy", action="store_false")
    render.set_defaults(require_spacy=None)
    diagnostics = render.add_mutually_exclusive_group()
    diagnostics.add_argument("--diagnostics", dest="diagnostics", action="store_true")
    diagnostics.add_argument("--no-diagnostics", dest="diagnostics", action="store_false")
    render.set_defaults(diagnostics=None)

    render.add_argument("--compose-sample-rate", type=int)
    render.add_argument("--compose-channels", type=int)
    render.add_argument("--clip-policy", choices=("clamp", "warn", "error"))
    render.add_argument("--target-lufs", type=float)
    render.add_argument("--true-peak-ceiling-dbtp", type=float)
    render.add_argument("--peak-policy", choices=("reduce_gain", "error"))
    collect_metrics = render.add_mutually_exclusive_group()
    collect_metrics.add_argument("--collect-metrics", dest="collect_metrics", action="store_true")
    collect_metrics.add_argument(
        "--no-collect-metrics", dest="collect_metrics", action="store_false"
    )
    render.set_defaults(collect_metrics=None)
    render.add_argument("--compose-emphasis-policy", choices=("segment_fallback", "strict"))
    render.add_argument("--media-root", type=Path)
    render.add_argument("--save-audiojob", type=Path)

    render.add_argument("--format")
    render.add_argument("--bitrate")
    render.add_argument("--export-sample-rate", type=int)
    render.add_argument("--export-channels", type=int)
    render.add_argument("--cover", type=Path)
    render.add_argument("--ffmpeg", type=Path)
    render.add_argument("--ffprobe", type=Path)
    render.add_argument("--json", action="store_true")
    render.add_argument("--no-progress", action="store_true")


def _metadata(values: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        key, separator, content = value.partition("=")
        if not separator or not key:
            raise CLIUsageError(f"metadata must use KEY=VALUE: {value!r}")
        parsed[key] = content
    return parsed


def _put(target: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current = target
    for key in path[:-1]:
        current = current.setdefault(key, {})
    current[path[-1]] = value


def _cli_overrides(args: argparse.Namespace) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    fields = (
        ("language", ("document", "language")),
        ("sequence_fallback_mode", ("document", "convert", "sequence_fallback_mode")),
        ("text_preparation", ("document", "plan", "text_preparation")),
        ("overlap_mode", ("document", "plan", "overlap_mode")),
        ("renderability_mode", ("document", "plan", "renderability_mode")),
        ("diagnostics", ("document", "plan", "diagnostics")),
        ("spacy_model", ("document", "plan", "linguistics", "spacy_model")),
        ("spacy_model_size", ("document", "plan", "linguistics", "spacy_model_size")),
        ("require_spacy", ("document", "plan", "linguistics", "require_spacy")),
        ("force", ("render", "force")),
        ("reset_stale", ("render", "reset_stale_output")),
        ("emphasis_policy", ("render", "emphasis_policy")),
        ("compose_sample_rate", ("compose", "sample_rate")),
        ("compose_channels", ("compose", "channels")),
        ("clip_policy", ("compose", "clip_policy")),
        ("target_lufs", ("compose", "target_lufs")),
        ("true_peak_ceiling_dbtp", ("compose", "true_peak_ceiling_dbtp")),
        ("peak_policy", ("compose", "peak_policy")),
        ("collect_metrics", ("compose", "collect_metrics")),
        ("compose_emphasis_policy", ("compose", "emphasis_policy")),
        ("format", ("export", "format")),
        ("bitrate", ("export", "bitrate")),
        ("export_sample_rate", ("export", "sample_rate")),
        ("export_channels", ("export", "channels")),
    )
    for argument, path in fields:
        value = getattr(args, argument)
        if value is not None:
            _put(overrides, path, value)
    if args.spacy is not None:
        use_spacy = {"auto": None, "on": True, "off": False}[args.spacy]
        _put(overrides, ("document", "plan", "linguistics", "use_spacy"), use_spacy)
    return overrides


def _settings(args: argparse.Namespace, api: Any) -> dict[str, Any]:
    if args.config is None:
        settings: dict[str, Any] = {}
    else:
        try:
            settings = api.load_settings(args.config)
        except (OSError, TypeError, ValueError) as error:
            raise ConfigurationError(str(error)) from error
    return api.merge_settings(settings, _cli_overrides(args))


def _render_request(args: argparse.Namespace, api: Any) -> DocumentRenderRequest:
    settings = _settings(args, api)
    document = settings.get("document", {})
    convert_settings = document.get("convert", {})
    plan_settings = document.get("plan", {})
    linguistics_settings = plan_settings.get("linguistics", {})
    render_settings = settings.get("render", {})
    compose_settings = settings.get("compose", {})
    export_settings = settings.get("export", {})

    metadata = _metadata(args.metadata)
    conversion_defaults = ssmdconvert.DocumentConversionOptions()
    conversion_options = ssmdconvert.DocumentConversionOptions(
        language=document.get("language", conversion_defaults.language),
        title=args.title,
        author=args.author,
        metadata_overrides=metadata,
        sequence_fallback_mode=convert_settings.get(
            "sequence_fallback_mode", conversion_defaults.sequence_fallback_mode
        ),
    )

    language = document.get("language") or conversion_options.language or "en-US"
    planner_defaults = utterplan.PlannerConfig(language=language, document_format="ssmd")
    linguistic_defaults = planner_defaults.linguistics
    linguistics = utterplan.LinguisticsConfig(
        use_spacy=linguistics_settings.get("use_spacy", linguistic_defaults.use_spacy),
        spacy_model=linguistics_settings.get("spacy_model", linguistic_defaults.spacy_model),
        spacy_model_size=linguistics_settings.get(
            "spacy_model_size", linguistic_defaults.spacy_model_size
        ),
        require_spacy=linguistics_settings.get("require_spacy", linguistic_defaults.require_spacy),
    )
    planner_config = utterplan.PlannerConfig(
        language=language,
        document_format="ssmd",
        text_preparation=plan_settings.get("text_preparation", planner_defaults.text_preparation),
        linguistics=linguistics,
        ssmd=planner_defaults.ssmd,
        overlap_mode=plan_settings.get("overlap_mode", planner_defaults.overlap_mode),
        renderability_mode=plan_settings.get(
            "renderability_mode", planner_defaults.renderability_mode
        ),
        language_aliases=planner_defaults.language_aliases,
        diagnostics=plan_settings.get("diagnostics", planner_defaults.diagnostics),
    )

    compose_defaults = audiocompose.OutputPolicy()
    loudness_defaults = compose_defaults.loudness
    loudness = audiocompose.LoudnessPolicy(
        target_lufs=compose_settings.get("target_lufs", loudness_defaults.target_lufs),
        true_peak_ceiling_dbtp=compose_settings.get(
            "true_peak_ceiling_dbtp", loudness_defaults.true_peak_ceiling_dbtp
        ),
        peak_policy=compose_settings.get("peak_policy", loudness_defaults.peak_policy),
        collect_metrics=compose_settings.get("collect_metrics", loudness_defaults.collect_metrics),
    )
    compose_policy = audiocompose.OutputPolicy(
        sample_rate=compose_settings.get("sample_rate", compose_defaults.sample_rate),
        channels=compose_settings.get("channels", compose_defaults.channels),
        loudness=loudness,
        clip_policy=compose_settings.get("clip_policy", compose_defaults.clip_policy),
    )

    return DocumentRenderRequest(
        source=args.source,
        profile=args.profile,
        output=args.output,
        artifacts=args.artifacts,
        keep_artifacts=args.keep_artifacts,
        clean_artifacts=args.clean_artifacts,
        stop_after=args.stop_after,
        conversion_options=conversion_options,
        planner_config=planner_config,
        cache_dir=args.cache_dir,
        speech_selector=args.speech,
        render_emphasis_policy=render_settings.get("emphasis_policy", "defer-to-compose"),
        reset_stale_output=render_settings.get("reset_stale_output", False),
        compose_output_policy=compose_policy,
        compose_emphasis_policy=compose_settings.get("emphasis_policy", "segment_fallback"),
        media_root=args.media_root,
        save_audiojob=args.save_audiojob,
        export_format=export_settings.get("format"),
        export_bitrate=export_settings.get("bitrate"),
        export_sample_rate=export_settings.get("sample_rate"),
        export_channels=export_settings.get("channels"),
        metadata=metadata,
        cover=args.cover,
        ffmpeg=args.ffmpeg,
        ffprobe=args.ffprobe,
        force=render_settings.get("force", False),
    )


def _progress(event: ProgressEvent) -> None:
    details = (
        f" {event.current}/{event.total}"
        if event.current is not None and event.total is not None
        else ""
    )
    message = f" {event.message}" if event.message else ""
    print(f"{event.stage}: {event.kind.removeprefix('stage.')}{details}{message}", file=sys.stderr)


def _summary(result: DocumentRenderResult, request: DocumentRenderRequest) -> dict[str, Any]:
    stage_results = (
        ("convert", result.conversion_result),
        ("plan", result.compile_result),
        ("render-plan", result.renderplan),
        ("synth", result.render_result),
        ("compose", result.compose_result),
        ("export", result.export_result),
    )
    return {
        "artifacts_dir": str(result.artifacts_dir),
        "completed_stages": [name for name, value in stage_results if value is not None],
        "stop_after": request.stop_after,
        "ssmd": str(result.ssmd_path) if result.ssmd_path else None,
        "utterplan": str(result.utterplan_path) if result.utterplan_path else None,
        "renderplan": str(result.renderplan_path) if result.renderplan_path else None,
        "preflight": str(result.preflight_path) if result.preflight_path else None,
        "render_dir": str(result.render_dir) if result.render_dir else None,
        "composed_wav": str(result.composed_wav) if result.composed_wav else None,
        "output": str(result.output) if result.output else None,
        "pending_composition": (
            request.stop_after == "synth" and request.render_emphasis_policy == "defer-to-compose"
        ),
    }


def run_document_command(args: argparse.Namespace, api: Any) -> int:
    request = _render_request(args, api)
    result = api.render_document(request, on_progress=None if args.no_progress else _progress)
    summary = _summary(result, request)
    if args.json:
        print(json.dumps({"ok": True, "result": summary}, ensure_ascii=False))
    else:
        print(f"Completed: {', '.join(summary['completed_stages']) or 'none'}")
        print(f"Artifacts: {summary['artifacts_dir']}")
        if result.output is not None:
            print(f"Output: {result.output}")
        if summary["pending_composition"]:
            print(
                "Warning: synthesis stopped before composition; deferred emphasis may not be realized.",
                file=sys.stderr,
            )
    return 0


__all__ = ["add_document_command", "run_document_command"]
