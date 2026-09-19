"""PiperSynth engine adapter for Readio.

This adapter implements the EngineAdapter protocol for PiperSynth,
mapping Piper voice bundles to the neutral engine contract.
"""

from __future__ import annotations

import importlib.metadata
import logging
import math
from collections.abc import Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from audiocompose import AudioJob
from utterplan import UtterancePlan

from ..config import normalize_language_key
from .base import EngineCapabilities, EngineSelection
from .catalog import CatalogRequest, SynthesisTarget

logger = logging.getLogger(__name__)

PIPER_RENDER_OPTIONS = frozenset(
    {
        "speaker",
        "length_scale",
        "noise_scale",
        "noise_w_scale",
        "normalize_audio",
        "volume",
        "sentence_silence",
    }
)


def _target_from_voice_metadata(metadata: Any, engine: str = "piper") -> SynthesisTarget:
    """Project PiperSynth metadata into Readio's neutral target type."""
    speaker_map = dict(getattr(metadata, "speaker_id_map", {}) or {})
    language_code = getattr(metadata, "language_code", None)
    return SynthesisTarget(
        engine=engine,
        id=metadata.id,
        display_name=getattr(metadata, "name", None) or metadata.id,
        languages=(language_code,) if language_code else (),
        status="ready",
        runtime_available=True,
        sample_rate=None,
        voices=(metadata.id,),
        speakers=tuple(str(name) for name in speaker_map),
        qualities=((metadata.quality,) if getattr(metadata, "quality", None) else ()),
        aliases=tuple(getattr(metadata, "aliases", ()) or ()),
        capabilities=frozenset(PIPER_RENDER_OPTIONS),
        metadata={
            "language_family": getattr(metadata, "language_family", None),
            "region": getattr(metadata, "region", None),
            "num_speakers": getattr(metadata, "num_speakers", 0),
            "speaker_id_map": speaker_map,
            "source_revision": getattr(metadata, "source_revision", None),
        },
    )


class PiperSynthEngineSession:
    """PiperSynth rendering session."""

    def __init__(self, pipeline: Any) -> None:
        self._pipeline = pipeline

    def prepare_plan(
        self,
        plan: UtterancePlan,
        *,
        options: Mapping[str, Any],
    ) -> AbstractContextManager[Any]:
        """Prepare renderer units from an existing UtterancePlan."""
        return self._pipeline.prepare_plan(plan, **dict(options))

    def to_audio_job(
        self,
        plan: UtterancePlan,
        *,
        options: Mapping[str, Any],
    ) -> AudioJob:
        """Create an AudioJob from an existing UtterancePlan."""
        return self._pipeline.to_audio_job(plan, **dict(options))


class PiperSynthEngineAdapter:
    """PiperSynth implementation of the EngineAdapter protocol."""

    id = "piper"
    package_name = "pipersynth"

    def version(self) -> str | None:
        try:
            return importlib.metadata.version("pipersynth")
        except importlib.metadata.PackageNotFoundError:
            return None

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            id=self.id,
            ssmd_provider="piper",
            option_names=PIPER_RENDER_OPTIONS,
            supports_prepared_units=True,
            supports_audio_job=True,
            supports_lexicons=False,
            supports_speakers=True,
            supports_model_sources=False,
            supports_qualities=True,
        )

    def discover(self, request: CatalogRequest) -> tuple[SynthesisTarget, ...]:
        """Discover Piper voice bundles through the public asset manager."""
        try:
            from pipersynth.asset_manager import VoiceAssetManager
        except ImportError:
            return ()

        manager = VoiceAssetManager(offline=getattr(request, "offline", False))
        voices = manager.list_voices(
            language=getattr(request, "language", None),
            refresh=getattr(request, "refresh", False),
        )
        return tuple(_target_from_voice_metadata(item, self.id) for item in voices)

    def resolve(
        self,
        request: Any,
    ) -> tuple[EngineSelection, tuple[Any, ...]]:
        """Resolve a concrete Piper voice-bundle selection."""
        engine = getattr(request, "engine", self.id) or self.id
        language = normalize_language_key(getattr(request, "language", None) or "en-us")
        voice = getattr(request, "voice", None)
        target_id = getattr(request, "target_id", None) or voice
        speaker = getattr(request, "speaker", None)
        if not target_id:
            raise ValueError(
                "piper.voice_required: Piper requires a voice bundle; "
                "pass --voice <id> or configure a Piper voice for this language. "
                f"Run `readio voices list --engine piper --lang {language}` to inspect voices."
            )

        raw_options = dict(getattr(request, "options", {}) or {})
        speed = raw_options.pop("speed", None)
        rate = raw_options.pop("rate", None)
        if speed is not None and rate is not None and speed != rate:
            raise ValueError("piper.speed_conflict: specify only one speed/rate value")
        resolved_speed = speed if speed is not None else rate
        options: dict[str, Any] = {}
        if resolved_speed is not None:
            try:
                resolved_speed = float(resolved_speed)
            except (TypeError, ValueError) as exc:
                raise ValueError("piper.invalid_speed: speed must be finite and > 0") from exc
            if not math.isfinite(resolved_speed) or resolved_speed <= 0:
                raise ValueError("piper.invalid_speed: speed must be finite and > 0")
            options["length_scale"] = 1.0 / resolved_speed

        incompatible = {
            "lexicons": bool,
            "model_source": lambda value: value not in (None, ""),
            "allow_experimental": bool,
        }
        for name, predicate in incompatible.items():
            value = raw_options.get(name)
            if value is not None and predicate(value):
                raise ValueError(
                    f"piper.option_not_supported: option {name!r} is not supported by engine piper"
                )
        for name in PIPER_RENDER_OPTIONS:
            if name in raw_options and raw_options[name] is not None:
                options[name] = raw_options[name]
        if speaker is not None:
            options["speaker"] = speaker

        selection = EngineSelection(
            engine=engine,
            target_id=target_id,
            language=language,
            voice=voice or target_id,
            speaker=speaker,
            options=options,
            offline=bool(getattr(request, "offline", False)),
            refresh=bool(getattr(request, "refresh", False)),
        )
        return selection, ()

    def validate_selection(self, selection: EngineSelection) -> tuple[Any, ...]:
        """Validate a Piper target from catalog metadata without loading ONNX."""
        from ..plan import PlanDiagnostic

        try:
            from pipersynth.asset_manager import VoiceAssetManager
        except ImportError:
            return (
                PlanDiagnostic(
                    code="piper.engine_unavailable",
                    severity="error",
                    message="Piper engine is unavailable because PiperSynth is not installed. "
                    'Install with: pip install "readio[piper]"',
                    field="synthesis.engine",
                ),
            )

        manager = VoiceAssetManager(offline=selection.offline)
        try:
            metadata = manager.get_voice_metadata(
                selection.target_id,
                refresh=selection.refresh,
            )
        except (KeyError, LookupError, OSError, ValueError) as exc:
            return (
                PlanDiagnostic(
                    code="piper.voice_not_found",
                    severity="error",
                    message=(
                        f"Piper voice bundle {selection.target_id!r} was not found. "
                        f"Run `readio voices list --engine piper --lang {selection.language}`. "
                        f"({exc})"
                    ),
                    field="render.target.id",
                ),
            )

        requested = normalize_language_key(selection.language)
        actual = normalize_language_key(metadata.language_code)
        requested_base = requested.split("-", 1)[0]
        actual_base = actual.split("-", 1)[0]
        if requested != actual and requested_base != actual_base:
            return (
                PlanDiagnostic(
                    code="piper.language_mismatch",
                    severity="error",
                    message=(
                        f"Piper voice {selection.target_id!r} is {metadata.language_code}, "
                        f"but the request is {selection.language}."
                    ),
                    field="render.target.language",
                ),
            )

        if selection.speaker is not None:
            speaker_map = dict(metadata.speaker_id_map or {})
            valid = selection.speaker in speaker_map or (
                isinstance(selection.speaker, int) and selection.speaker in speaker_map.values()
            )
            if not valid:
                return (
                    PlanDiagnostic(
                        code="piper.speaker_not_found",
                        severity="error",
                        message=(
                            f"Speaker {selection.speaker!r} is not available in "
                            f"Piper voice {selection.target_id!r}."
                        ),
                        field="render.target.speaker",
                    ),
                )
        return ()

    def target_metadata(self, selection: EngineSelection) -> Mapping[str, Any]:
        """Return stable catalog provenance for a resolved Piper target."""
        try:
            from pipersynth.asset_manager import VoiceAssetManager

            metadata = VoiceAssetManager(offline=selection.offline).get_voice_metadata(
                selection.target_id,
                refresh=selection.refresh,
            )
        except (ImportError, KeyError, LookupError, OSError, ValueError):
            return {}
        return {
            "language_family": metadata.language_family,
            "region": metadata.region,
            "num_speakers": metadata.num_speakers,
            "speaker_id_map": dict(metadata.speaker_id_map),
            "source_revision": metadata.source_revision,
            "quality": metadata.quality,
        }

    def planner_config(
        self,
        selection: EngineSelection,
        planning: Any,
    ) -> Any:
        """Piper uses Readio's engine-neutral semantic planning policy."""
        return None

    def open(
        self,
        selection: EngineSelection,
    ) -> AbstractContextManager[PiperSynthEngineSession]:
        """Open PiperSynth only after semantic planning is complete."""
        from pipersynth import GenerationConfig, PiperPipeline

        options = dict(selection.options)
        generation = GenerationConfig(
            speaker=options.get("speaker", selection.speaker),
            length_scale=options.get("length_scale"),
            noise_scale=options.get("noise_scale"),
            noise_w_scale=options.get("noise_w_scale"),
            normalize_audio=bool(options.get("normalize_audio", True)),
            volume=float(options.get("volume", 1.0)),
            sentence_silence=float(options.get("sentence_silence", 0.0)),
        )
        pipeline = PiperPipeline.from_pretrained(
            voice=selection.target_id,
            language=selection.language,
            offline=selection.offline,
            refresh_catalog=selection.refresh,
            generation=generation,
        )

        @contextmanager
        def _session() -> Any:
            try:
                yield PiperSynthEngineSession(pipeline)
            finally:
                pipeline.close()

        return _session()


__all__ = ["PiperSynthEngineAdapter"]
