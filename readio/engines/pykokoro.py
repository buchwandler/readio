"""PyKokoro engine adapter for Readio.

This adapter implements the new EngineAdapter protocol for PyKokoro,
mapping PyKokoro's concepts to the neutral engine contract.
"""

from __future__ import annotations

import importlib.metadata
import logging
from collections.abc import Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from utterplan import UtterancePlan

from audiocompose import AudioJob

from .base import EngineAdapter, EngineCapabilities, EngineSelection, EngineSession
from .catalog import SynthesisTarget

logger = logging.getLogger(__name__)


class PyKokoroEngineSession:
    """PyKokoro rendering session."""

    def __init__(self, pipeline: Any) -> None:
        self._pipeline = pipeline

    def prepare_plan(
        self,
        plan: UtterancePlan,
        *,
        options: Mapping[str, Any],
    ) -> AbstractContextManager[Any]:
        """Prepare renderer units from an existing UtterancePlan."""
        return self._pipeline.prepare_plan_units(plan, **dict(options))

    def to_audio_job(
        self,
        plan: UtterancePlan,
        *,
        options: Mapping[str, Any],
    ) -> AudioJob:
        """Create an AudioJob from an existing UtterancePlan."""
        return self._pipeline.to_audio_job_from_plan(plan, **dict(options))


class PyKokoroEngineAdapter:
    """PyKokoro implementation of the EngineAdapter protocol."""

    id = "pykokoro"

    def version(self) -> str | None:
        try:
            return importlib.metadata.version("pykokoro")
        except importlib.metadata.PackageNotFoundError:
            return None

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            id=self.id,
            ssmd_provider="kokoro",
            option_names=frozenset({
                "lexicons",
                "model_source",
                "g2p_fallback",
                "lexicon_data_policy",
                "spacy",
                "short_sentence",
                "language_detection",
                "detect_language",
            }),
            supports_prepared_units=True,
            supports_audio_job=True,
            supports_lexicons=True,
            supports_speakers=False,
            supports_model_sources=True,
            supports_qualities=True,
        )

    def discover(self, request: Any) -> Any:
        """Discover available PyKokoro models."""
        from ..models import _discover_pykokoro_model_info

        language = getattr(request, "language", None)
        offline = getattr(request, "offline", False)
        refresh = getattr(request, "refresh", False)

        models, result = _discover_pykokoro_model_info(
            language=language,
            offline=offline,
            refresh=refresh,
        )

        targets = tuple(
            SynthesisTarget(
                engine=self.id,
                id=model.id,
                display_name=model.id,
                languages=model.languages,
                status=model.status,
                runtime_available=model.runtime_available,
                sample_rate=None,
                voices=model.voices,
                speakers=(),
                qualities=model.qualities,
                aliases=model.aliases if hasattr(model, "aliases") else (),
                capabilities=frozenset({"lexicons", "model_source", "qualities"}),
                metadata={
                    "source": model.source,
                    "g2p_backend": model.g2p_backend,
                    "frontend": model.frontend,
                },
            )
            for model in models
        )

        return targets

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

        selection = EngineSelection(
            engine=engine,
            target_id=getattr(request, "target_id", "default"),
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
        from pykokoro.planning import planner_config_from_pipeline
        from pykokoro import PipelineConfig

        # Create a minimal pipeline config for planner config extraction
        cfg = PipelineConfig(
            voice=selection.voice,
            generation=None,
        )
        return planner_config_from_pipeline(cfg)

    def open(
        self,
        selection: EngineSelection,
    ) -> AbstractContextManager[PyKokoroEngineSession]:
        """Open a rendering session for the given selection."""
        from pykokoro import GenerationConfig, KokoroPipeline, PipelineConfig

        cfg = PipelineConfig(
            voice=selection.voice,
            generation=GenerationConfig(
                lang=selection.language,
            ),
        )
        pipeline = KokoroPipeline(cfg)

        @contextmanager
        def _session() -> Any:
            try:
                yield PyKokoroEngineSession(pipeline)
            finally:
                pass  # Pipeline cleanup if needed

        return _session()


__all__ = ["PyKokoroEngineAdapter"]
