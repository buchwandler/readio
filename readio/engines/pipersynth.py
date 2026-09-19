"""PiperSynth engine adapter for Readio.

This adapter implements the EngineAdapter protocol for PiperSynth,
mapping Piper voice bundles to the neutral engine contract.
"""

from __future__ import annotations

import importlib.metadata
import logging
from collections.abc import Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from audiocompose import AudioJob
from utterplan import UtterancePlan

from .base import EngineCapabilities, EngineSelection
from .catalog import SynthesisTarget

logger = logging.getLogger(__name__)


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

    def version(self) -> str | None:
        try:
            return importlib.metadata.version("pipersynth")
        except importlib.metadata.PackageNotFoundError:
            return None

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            id=self.id,
            ssmd_provider="piper",
            option_names=frozenset(
                {
                    "speaker",
                    "noise_scale",
                    "noise_w_scale",
                    "length_scale",
                    "normalize_audio",
                }
            ),
            supports_prepared_units=True,
            supports_audio_job=True,
            supports_lexicons=False,
            supports_speakers=True,
            supports_model_sources=False,
            supports_qualities=True,
        )

    def discover(self, request: Any) -> Any:
        """Discover available Piper voice bundles."""
        try:
            from pipersynth.asset_manager import VoiceAssetManager
        except ImportError:
            return ()

        language = getattr(request, "language", None)
        offline = getattr(request, "offline", False)
        refresh = getattr(request, "refresh", False)

        manager = VoiceAssetManager(offline=offline)
        catalog = manager.catalog(refresh=refresh)

        targets = []
        for voice_id, voice_info in catalog.items():
            voice_languages = voice_info.get("languages", [])
            if language and language not in voice_languages:
                continue

            speakers = voice_info.get("speakers", [])
            quality = voice_info.get("quality", "medium")

            targets.append(
                SynthesisTarget(
                    engine=self.id,
                    id=voice_id,
                    display_name=voice_info.get("name", voice_id),
                    languages=tuple(voice_languages),
                    status="ready",
                    runtime_available=True,
                    sample_rate=voice_info.get("sample_rate"),
                    voices=(voice_id,),
                    speakers=tuple(speakers),
                    qualities=(quality,) if quality else (),
                    aliases=tuple(voice_info.get("aliases", [])),
                    capabilities=frozenset({"speaker", "noise_scale", "length_scale"}),
                    metadata={
                        "source_revision": voice_info.get("source_revision"),
                        "num_speakers": voice_info.get("num_speakers", 0),
                    },
                )
            )

        return tuple(targets)

    def resolve(
        self,
        request: Any,
    ) -> tuple[EngineSelection, tuple[Any, ...]]:
        """Resolve a concrete selection from a request."""
        engine = getattr(request, "engine", self.id)
        language = getattr(request, "language", "en-us")
        voice = getattr(request, "voice", None)
        speaker = getattr(request, "speaker", None)
        options = dict(getattr(request, "options", {}))

        # Map rate to length_scale if present
        rate = options.pop("rate", None)
        if rate is not None:
            options["length_scale"] = 1.0 / rate

        selection = EngineSelection(
            engine=engine,
            target_id=voice or "default",
            language=language,
            voice=voice,
            speaker=speaker,
            options=options,
        )

        return selection, ()

    def planner_config(
        self,
        selection: EngineSelection,
        planning: Any,
    ) -> Any:
        """Return the planner configuration needed for this selection."""
        try:
            from pipersynth.config import PipelineConfig
            from pipersynth.planning import planner_config_from_pipersynth
        except ImportError:
            return None

        cfg = PipelineConfig(
            language=selection.language,
        )
        return planner_config_from_pipersynth(cfg)

    def open(
        self,
        selection: EngineSelection,
    ) -> AbstractContextManager[PiperSynthEngineSession]:
        """Open a rendering session for the given selection."""
        from pipersynth import GenerationConfig, PiperPipeline

        options = dict(selection.options)
        generation = GenerationConfig(
            speaker=selection.speaker,
            length_scale=options.get("length_scale"),
            noise_scale=options.get("noise_scale"),
            noise_w_scale=options.get("noise_w_scale"),
            normalize_audio=bool(options.get("normalize_audio", True)),
        )
        pipeline = PiperPipeline.from_pretrained(
            voice=selection.voice or selection.target_id,
            language=selection.language,
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
