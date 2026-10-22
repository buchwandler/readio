"""Domain model and creation helpers for declarative Readio render intents."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from readio.workspace import default_bundle_workspace_path

IntentKind = Literal["book", "document"]
INTENT_SCHEMA = "readio.render.v1"

DIAGNOSTICS_MODES = ("errors", "warnings", "all", "none")
TEXT_PREPARATIONS = ("auto", "ssmd", "spokenform", "identity")
OVERLAP_MODES = ("snap", "strict")
RENDERABILITY_MODES = ("repair", "strict")
SPACY_MODES = ("auto", "on", "off")
SPACY_MODEL_SIZES = ("sm", "md", "lg", "trf")
COMPOSE_EMPHASIS_POLICIES = ("segment_fallback", "strict")
CLIP_POLICIES = ("clamp", "warn", "error")
PEAK_POLICIES = ("reduce_gain", "error")

_CHAPTER_SELECTOR = re.compile(
    r"(?:all|[1-9][0-9]*(?:-[1-9][0-9]*)?)(?:,[1-9][0-9]*(?:-[1-9][0-9]*)?)*\Z"
)
_FORMAT_EXTENSIONS = {
    "wave": "wav",
    "mpeg4": "m4a",
}


def _path(value: str | Path | None, field_name: str) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, (str, Path)):
        raise TypeError(f"{field_name} must be a path string")
    text = str(value)
    if not text.strip():
        raise ValueError(f"{field_name} must not be empty")
    return Path(value).expanduser()


def _nonempty(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def _choice(value: str, choices: tuple[str, ...], field_name: str) -> str:
    if value not in choices:
        raise ValueError(f"{field_name} must be one of: {', '.join(choices)}")
    return value


def _positive_int(value: int | None, field_name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class InputIntent:
    source: Path | None = None
    bundle: Path | None = None
    chapters: str | None = None
    language: str | None = None


@dataclass(frozen=True, slots=True)
class VoiceIntent:
    profile: Path | None = None
    variant: str | None = None


@dataclass(frozen=True, slots=True)
class PlanningIntent:
    diagnostics: str = "errors"
    text_preparation: str = "auto"
    overlap_mode: str = "snap"
    renderability_mode: str = "repair"
    spacy: str = "auto"
    spacy_model: str | None = None
    spacy_model_size: str | None = None
    require_spacy: bool = False


@dataclass(frozen=True, slots=True)
class RenderOptionsIntent:
    speech: str = "all"
    emphasis_policy: str = "defer-to-compose"


@dataclass(frozen=True, slots=True)
class ComposeIntent:
    emphasis_policy: str = "segment_fallback"
    sample_rate: int | None = 24000
    target_lufs: float | None = -18.0
    true_peak_ceiling_dbtp: float | None = None
    clip_policy: str | None = None
    peak_policy: str | None = None


@dataclass(frozen=True, slots=True)
class OutputIntent:
    format: str = "auto"
    path: Path | None = None
    bitrate: str | int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    cover: Path | None = None
    timeline: Path | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class RenderIntent:
    """User-owned desired render state, distinct from a resolved RunRequest."""

    kind: IntentKind | None = None
    input: InputIntent = field(default_factory=InputIntent)
    voice: VoiceIntent = field(default_factory=VoiceIntent)
    planning: PlanningIntent = field(default_factory=PlanningIntent)
    render: RenderOptionsIntent = field(default_factory=RenderOptionsIntent)
    compose: ComposeIntent = field(default_factory=ComposeIntent)
    output: OutputIntent = field(default_factory=OutputIntent)
    schema: str = INTENT_SCHEMA


def _default_stem(path: Path) -> str:
    name = path.name
    if name.casefold().endswith(".ssmd-bundle"):
        return name[: -len(".ssmd-bundle")]
    if name.casefold().endswith(".ssmd.md"):
        return name[: -len(".ssmd.md")]
    return path.stem


def _default_output_path(source: Path, kind: IntentKind, output_format: str) -> Path:
    selected_format = output_format.casefold()
    if selected_format == "auto":
        selected_format = "m4b" if kind == "book" else "mp3"
    suffix = _FORMAT_EXTENSIONS.get(selected_format.casefold(), selected_format.casefold())
    return source.parent / f"{_default_stem(source)}.{suffix}"


def _default_bundle_path(source: Path) -> Path:
    if source.name.casefold().endswith(".ssmd-bundle"):
        return source
    return default_bundle_workspace_path(source)


def _metadata(values: Mapping[str, str] | None) -> Mapping[str, str]:
    result = dict(values or {})
    for key, value in result.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("metadata keys must be non-empty strings")
        if not isinstance(value, str):
            raise TypeError(f"metadata value for {key!r} must be a string")
    return MappingProxyType(result)


def _create_intent(
    kind: IntentKind,
    source: str | Path,
    *,
    profile: str | Path,
    bundle: str | Path | None,
    chapters: str | None,
    language: str | None,
    variant: str | None,
    diagnostics: str,
    text_preparation: str,
    overlap_mode: str,
    renderability_mode: str,
    spacy: str,
    spacy_model: str | None,
    spacy_model_size: str | None,
    require_spacy: bool,
    speech: str,
    emphasis_policy: str,
    compose_emphasis_policy: str,
    compose_sample_rate: int | None,
    target_lufs: float | None,
    true_peak_ceiling_dbtp: float | None,
    clip_policy: str | None,
    peak_policy: str | None,
    output_format: str,
    output: str | Path | None,
    bitrate: str | int | None,
    export_sample_rate: int | None,
    channels: int | None,
    cover: str | Path | None,
    timeline: str | Path | None,
    metadata: Mapping[str, str] | None,
) -> RenderIntent:
    source_path = _path(source, "input.source")
    profile_path = _path(profile, "voice.profile")
    assert source_path is not None
    input_intent = InputIntent(
        source=source_path,
        bundle=_path(bundle, "input.bundle") or _default_bundle_path(source_path),
        chapters=chapters,
        language=_nonempty(language, "input.language"),
    )
    voice_intent = VoiceIntent(
        profile=profile_path,
        variant=_nonempty(variant, "voice.variant"),
    )
    planning = PlanningIntent(
        diagnostics=_choice(diagnostics, DIAGNOSTICS_MODES, "planning.diagnostics"),
        text_preparation=_choice(text_preparation, TEXT_PREPARATIONS, "planning.text_preparation"),
        overlap_mode=_choice(overlap_mode, OVERLAP_MODES, "planning.overlap_mode"),
        renderability_mode=_choice(
            renderability_mode, RENDERABILITY_MODES, "planning.renderability_mode"
        ),
        spacy=_choice(spacy, SPACY_MODES, "planning.spacy"),
        spacy_model=_nonempty(spacy_model, "planning.spacy_model"),
        spacy_model_size=(
            _choice(spacy_model_size, SPACY_MODEL_SIZES, "planning.spacy_model_size")
            if spacy_model_size is not None
            else None
        ),
        require_spacy=require_spacy,
    )
    if type(require_spacy) is not bool:
        raise TypeError("planning.require_spacy must be a boolean")
    if kind == "document" and chapters is not None:
        raise ValueError("chapters selection is available only for book intents")
    if chapters is not None and not _CHAPTER_SELECTOR.fullmatch(chapters):
        raise ValueError("input.chapters must be 'all' or a comma-separated chapter selector")
    if not isinstance(speech, str) or not speech.strip():
        raise ValueError("render.speech must be a non-empty string")
    if not isinstance(emphasis_policy, str) or not emphasis_policy.strip():
        raise ValueError("render.emphasis_policy must be a non-empty string")
    if not isinstance(output_format, str) or not output_format.strip():
        raise ValueError("output.format must be a non-empty string")
    if kind == "book" and output_format.casefold() not in {"auto", "m4b"}:
        raise ValueError("book intents support only the m4b output format")
    compose = ComposeIntent(
        emphasis_policy=_choice(
            compose_emphasis_policy, COMPOSE_EMPHASIS_POLICIES, "compose.emphasis_policy"
        ),
        sample_rate=_positive_int(compose_sample_rate, "compose.sample_rate"),
        target_lufs=target_lufs,
        true_peak_ceiling_dbtp=true_peak_ceiling_dbtp,
        clip_policy=(
            _choice(clip_policy, CLIP_POLICIES, "compose.clip_policy")
            if clip_policy is not None
            else None
        ),
        peak_policy=(
            _choice(peak_policy, PEAK_POLICIES, "compose.peak_policy")
            if peak_policy is not None
            else None
        ),
    )
    for field_name, value in (
        ("compose.target_lufs", target_lufs),
        ("compose.true_peak_ceiling_dbtp", true_peak_ceiling_dbtp),
    ):
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError(f"{field_name} must be a finite number")
    if isinstance(bitrate, str) and not bitrate.strip():
        raise ValueError("output.bitrate must not be empty")
    if bitrate is not None and not isinstance(bitrate, (str, int)):
        raise TypeError("output.bitrate must be a string or integer")
    if isinstance(bitrate, int) and bitrate <= 0:
        raise ValueError("output.bitrate must be positive")
    output_path = _path(output, "output.path") or _default_output_path(
        source_path, kind, output_format
    )
    intent = RenderIntent(
        kind=kind,
        input=input_intent,
        voice=voice_intent,
        planning=planning,
        render=RenderOptionsIntent(speech=speech, emphasis_policy=emphasis_policy),
        compose=compose,
        output=OutputIntent(
            format=output_format,
            path=output_path,
            bitrate=bitrate,
            sample_rate=_positive_int(export_sample_rate, "output.sample_rate"),
            channels=_positive_int(channels, "output.channels"),
            cover=_path(cover, "output.cover"),
            timeline=_path(timeline, "output.timeline"),
            metadata=_metadata(metadata),
        ),
    )
    validate_intent_shape(intent)
    return intent


def create_book_intent(
    source: str | Path,
    *,
    profile: str | Path,
    bundle: str | Path | None = None,
    chapters: str = "all",
    language: str | None = None,
    variant: str | None = None,
    diagnostics: str = "errors",
    text_preparation: str = "auto",
    overlap_mode: str = "snap",
    renderability_mode: str = "repair",
    spacy: str = "auto",
    spacy_model: str | None = None,
    spacy_model_size: str | None = None,
    require_spacy: bool = False,
    speech: str = "all",
    emphasis_policy: str = "defer-to-compose",
    compose_emphasis_policy: str = "segment_fallback",
    compose_sample_rate: int | None = 24000,
    target_lufs: float | None = -18.0,
    true_peak_ceiling_dbtp: float | None = None,
    clip_policy: str | None = None,
    peak_policy: str | None = None,
    output_format: str = "m4b",
    output: str | Path | None = None,
    bitrate: str | int | None = "96k",
    export_sample_rate: int | None = None,
    channels: int | None = None,
    cover: str | Path | None = None,
    timeline: str | Path | None = None,
    metadata: Mapping[str, str] | None = None,
) -> RenderIntent:
    """Build book intent values without converting, rendering, or writing files."""
    return _create_intent(
        "book",
        source,
        profile=profile,
        bundle=bundle,
        chapters=chapters,
        language=language,
        variant=variant,
        diagnostics=diagnostics,
        text_preparation=text_preparation,
        overlap_mode=overlap_mode,
        renderability_mode=renderability_mode,
        spacy=spacy,
        spacy_model=spacy_model,
        spacy_model_size=spacy_model_size,
        require_spacy=require_spacy,
        speech=speech,
        emphasis_policy=emphasis_policy,
        compose_emphasis_policy=compose_emphasis_policy,
        compose_sample_rate=compose_sample_rate,
        target_lufs=target_lufs,
        true_peak_ceiling_dbtp=true_peak_ceiling_dbtp,
        clip_policy=clip_policy,
        peak_policy=peak_policy,
        output_format=output_format,
        output=output,
        bitrate=bitrate,
        export_sample_rate=export_sample_rate,
        channels=channels,
        cover=cover,
        timeline=timeline,
        metadata=metadata,
    )


def create_document_intent(
    source: str | Path,
    *,
    profile: str | Path,
    bundle: str | Path | None = None,
    language: str | None = None,
    variant: str | None = None,
    diagnostics: str = "errors",
    text_preparation: str = "auto",
    overlap_mode: str = "snap",
    renderability_mode: str = "repair",
    spacy: str = "auto",
    spacy_model: str | None = None,
    spacy_model_size: str | None = None,
    require_spacy: bool = False,
    speech: str = "all",
    emphasis_policy: str = "defer-to-compose",
    compose_emphasis_policy: str = "segment_fallback",
    compose_sample_rate: int | None = 24000,
    target_lufs: float | None = -18.0,
    true_peak_ceiling_dbtp: float | None = None,
    clip_policy: str | None = None,
    peak_policy: str | None = None,
    output_format: str = "mp3",
    output: str | Path | None = None,
    bitrate: str | int | None = None,
    export_sample_rate: int | None = None,
    channels: int | None = None,
    cover: str | Path | None = None,
    timeline: str | Path | None = None,
    metadata: Mapping[str, str] | None = None,
) -> RenderIntent:
    """Build document intent values without converting, rendering, or writing files."""
    return _create_intent(
        "document",
        source,
        profile=profile,
        bundle=bundle,
        chapters=None,
        language=language,
        variant=variant,
        diagnostics=diagnostics,
        text_preparation=text_preparation,
        overlap_mode=overlap_mode,
        renderability_mode=renderability_mode,
        spacy=spacy,
        spacy_model=spacy_model,
        spacy_model_size=spacy_model_size,
        require_spacy=require_spacy,
        speech=speech,
        emphasis_policy=emphasis_policy,
        compose_emphasis_policy=compose_emphasis_policy,
        compose_sample_rate=compose_sample_rate,
        target_lufs=target_lufs,
        true_peak_ceiling_dbtp=true_peak_ceiling_dbtp,
        clip_policy=clip_policy,
        peak_policy=peak_policy,
        output_format=output_format,
        output=output,
        bitrate=bitrate,
        export_sample_rate=export_sample_rate,
        channels=channels,
        cover=cover,
        timeline=timeline,
        metadata=metadata,
    )


def validate_intent_shape(intent: RenderIntent) -> None:
    """Validate in-memory values without probing files, profiles, or specialist engines."""
    if intent.schema != INTENT_SCHEMA:
        raise ValueError(f"schema must be exactly {INTENT_SCHEMA!r}")
    if intent.kind not in {None, "book", "document"}:
        raise ValueError("kind must be 'book' or 'document'")
    if intent.input.source is not None and not isinstance(intent.input.source, Path):
        raise TypeError("input.source must be a path")
    if intent.input.bundle is not None and not isinstance(intent.input.bundle, Path):
        raise TypeError("input.bundle must be a path")
    if intent.voice.profile is not None and not isinstance(intent.voice.profile, Path):
        raise TypeError("voice.profile must be a path")
    if intent.input.chapters is not None:
        if intent.kind == "document":
            raise ValueError("input.chapters is not valid for document intents")
        if not isinstance(intent.input.chapters, str) or not _CHAPTER_SELECTOR.fullmatch(
            intent.input.chapters
        ):
            raise ValueError("input.chapters must be 'all' or a comma-separated chapter selector")
    _nonempty(intent.input.language, "input.language")
    _nonempty(intent.voice.variant, "voice.variant")
    _choice(intent.planning.diagnostics, DIAGNOSTICS_MODES, "planning.diagnostics")
    _choice(intent.planning.text_preparation, TEXT_PREPARATIONS, "planning.text_preparation")
    _choice(intent.planning.overlap_mode, OVERLAP_MODES, "planning.overlap_mode")
    _choice(intent.planning.renderability_mode, RENDERABILITY_MODES, "planning.renderability_mode")
    _choice(intent.planning.spacy, SPACY_MODES, "planning.spacy")
    _nonempty(intent.planning.spacy_model, "planning.spacy_model")
    if intent.planning.spacy_model_size is not None:
        _choice(intent.planning.spacy_model_size, SPACY_MODEL_SIZES, "planning.spacy_model_size")
    if type(intent.planning.require_spacy) is not bool:
        raise TypeError("planning.require_spacy must be a boolean")
    _nonempty(intent.render.speech, "render.speech")
    _nonempty(intent.render.emphasis_policy, "render.emphasis_policy")
    _choice(intent.compose.emphasis_policy, COMPOSE_EMPHASIS_POLICIES, "compose.emphasis_policy")
    _positive_int(intent.compose.sample_rate, "compose.sample_rate")
    for name, value in (
        ("compose.target_lufs", intent.compose.target_lufs),
        ("compose.true_peak_ceiling_dbtp", intent.compose.true_peak_ceiling_dbtp),
    ):
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError(f"{name} must be a finite number")
    if intent.compose.clip_policy is not None:
        _choice(intent.compose.clip_policy, CLIP_POLICIES, "compose.clip_policy")
    if intent.compose.peak_policy is not None:
        _choice(intent.compose.peak_policy, PEAK_POLICIES, "compose.peak_policy")
    _nonempty(intent.output.format, "output.format")
    if intent.kind == "book" and intent.output.format.casefold() not in {"auto", "m4b"}:
        raise ValueError("book intents support only the m4b output format")
    _positive_int(intent.output.sample_rate, "output.sample_rate")
    _positive_int(intent.output.channels, "output.channels")
    if intent.output.path is not None and not isinstance(intent.output.path, Path):
        raise TypeError("output.path must be a path")
    if intent.output.cover is not None and not isinstance(intent.output.cover, Path):
        raise TypeError("output.cover must be a path")
    if intent.output.timeline is not None and not isinstance(intent.output.timeline, Path):
        raise TypeError("output.timeline must be a path")
    if intent.output.bitrate is not None:
        if not isinstance(intent.output.bitrate, (str, int)) or isinstance(
            intent.output.bitrate, bool
        ):
            raise TypeError("output.bitrate must be a string or integer")
        if isinstance(intent.output.bitrate, str) and not intent.output.bitrate.strip():
            raise ValueError("output.bitrate must not be empty")
        if isinstance(intent.output.bitrate, int) and intent.output.bitrate <= 0:
            raise ValueError("output.bitrate must be positive")
    for key, value in intent.output.metadata.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(value, str):
            raise ValueError("output.metadata must map non-empty strings to strings")


def validate_render_intent(
    intent: RenderIntent,
    *,
    config_path: str | Path | None = None,
) -> None:
    """Validate intent values and their source/profile files without executing a render."""
    validate_intent_shape(intent)
    base = (
        Path(config_path).expanduser().resolve().parent if config_path is not None else Path.cwd()
    )

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else (base / path).resolve()

    source = intent.input.source or intent.input.bundle
    if source is None:
        raise ValueError("an input.source or input.bundle is required")
    source_path = resolve(source)
    if not source_path.exists():
        raise FileNotFoundError(f"render source does not exist: {source_path}")
    if intent.voice.profile is None:
        raise ValueError("voice.profile is required")
    profile_path = resolve(intent.voice.profile)
    if not profile_path.is_file():
        raise FileNotFoundError(f"VoiceRender RenderProfile does not exist: {profile_path}")
    from readio.profiles import validate_profile

    validate_profile(profile_path)
    if intent.output.path is not None:
        output_format = intent.output.format
        if output_format == "auto":
            output_format = "m4b" if intent.kind == "book" else "mp3"
        if output_format != "auto":
            suffix = _FORMAT_EXTENSIONS.get(output_format.casefold(), output_format.casefold())
            if intent.output.path.suffix.casefold() != f".{suffix}":
                raise ValueError(
                    f"output.path extension must match output.format {intent.output.format!r}"
                )


__all__ = [
    "CLIP_POLICIES",
    "COMPOSE_EMPHASIS_POLICIES",
    "DIAGNOSTICS_MODES",
    "INTENT_SCHEMA",
    "OVERLAP_MODES",
    "PEAK_POLICIES",
    "RENDERABILITY_MODES",
    "SPACY_MODEL_SIZES",
    "SPACY_MODES",
    "TEXT_PREPARATIONS",
    "ComposeIntent",
    "InputIntent",
    "IntentKind",
    "OutputIntent",
    "PlanningIntent",
    "RenderIntent",
    "RenderOptionsIntent",
    "VoiceIntent",
    "create_book_intent",
    "create_document_intent",
    "validate_intent_shape",
    "validate_render_intent",
]
