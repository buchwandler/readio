"""PyKokoro engine adapter for Readio.

This adapter implements the new EngineAdapter protocol for PyKokoro,
mapping PyKokoro's concepts to the neutral engine contract.
"""

from __future__ import annotations

import importlib.metadata
import logging
from collections.abc import Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from audiocompose import AudioJob
from utterplan import UtterancePlan

from .base import EngineCapabilities, EngineSelection
from .catalog import SynthesisTarget

if TYPE_CHECKING:
    from ..lexicons import LexiconCatalogEntry
    from ..plan import PlanDiagnostic, SynthesisCandidate

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
    package_name = "pykokoro"

    def version(self) -> str | None:
        try:
            return importlib.metadata.version("pykokoro")
        except importlib.metadata.PackageNotFoundError:
            return None

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            id=self.id,
            ssmd_provider="kokoro",
            option_names=frozenset(
                {
                    "lexicons",
                    "model_source",
                    "g2p_fallback",
                    "lexicon_data_policy",
                    "spacy",
                    "short_sentence",
                    "language_detection",
                    "detect_language",
                }
            ),
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

        models, _result = _discover_pykokoro_model_info(
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

    def discover_lexicons(
        self,
        *,
        language: str | None = None,
        model: str | None = None,
        offline: bool = False,
        refresh: bool = False,
        preference: str = "auto",
    ) -> tuple[tuple[LexiconCatalogEntry, ...], Any]:
        """Discover available lexicons for this engine."""
        from pykokoro import discover_lexicons

        from ..lexicons import LexiconCatalogEntry

        result = discover_lexicons(
            language=language,
            model_variant=model,
            offline=offline,
            refresh=refresh,
            preference=preference,
        )
        entries = tuple(
            LexiconCatalogEntry(
                selector=item.selector,
                engine=self.id,
                language=item.language,
                locale=item.locale,
                asset_id=item.asset_id,
                data_backend=item.data_backend,
                default=item.default,
                installed=item.installed,
                models=item.models,
                model_support=item.model_support,
                display_name=item.display_name,
                phoneme_encoding=item.phoneme_encoding,
                data_version=item.data_version,
            )
            for item in result.lexicons
        )
        return entries, result

    def resolve_defaults(
        self, candidate: SynthesisCandidate
    ) -> tuple[SynthesisCandidate, tuple[PlanDiagnostic, ...]]:
        """Resolve automatic defaults for a synthesis candidate."""
        if candidate.model is not None:
            return candidate, ()
        from pykokoro import (
            GenerationConfig,
            LanguageDetectionConfig,
            PipelineConfig,
            resolve_pipeline_config,
        )

        from ..reader import tokenizer_config_for_synthesis

        try:
            requested = PipelineConfig(
                voice=candidate.voice,
                model_source=candidate.source,
                model_variant=candidate.model,
                model_quality=candidate.quality,
                allow_experimental_frontend=candidate.allow_experimental,
                generation=GenerationConfig(
                    lang=candidate.language,
                    speed=candidate.speed,
                    pause_mode=candidate.pause_mode,
                ),
                tokenizer_config=tokenizer_config_for_synthesis(candidate),
                language_detection=(
                    None
                    if candidate.language_detection is None
                    else LanguageDetectionConfig(
                        mode=candidate.language_detection,
                        languages=tuple(candidate.detect_languages or ()),
                    )
                ),
            )
            resolved = resolve_pipeline_config(requested)
        except (ImportError, AttributeError, ValueError, TypeError) as exc:
            from ..plan import PlanDiagnostic

            return candidate, (
                PlanDiagnostic(
                    code="backend_resolution_failed",
                    severity="error",
                    message=f"PyKokoro pipeline resolution failed: {exc}",
                    field="synthesis.model",
                ),
            )

        from ..plan import ORIGIN_PYKOKORO_AUTO, ResolutionDecision

        decisions = list(candidate.decisions)
        automatic = (
            ("model", candidate.model, resolved.model_variant),
            ("source", candidate.source, resolved.model_source),
            ("quality", candidate.quality, resolved.model_quality),
            ("voice", candidate.voice, resolved.voice),
        )
        for field, previous, value in automatic:
            if previous is None and value is not None:
                decisions.append(
                    ResolutionDecision(
                        field=f"synthesis.{field}",
                        value=value,
                        origin=ORIGIN_PYKOKORO_AUTO,
                        reason=f"{field} was automatic before PyKokoro pipeline resolution",
                    )
                )
        return replace(
            candidate,
            model=resolved.model_variant,
            source=resolved.model_source,
            quality=resolved.model_quality,
            voice=resolved.voice,
            decisions=tuple(decisions),
        ), ()

    def validate_selection(self, selection: Any) -> tuple[PlanDiagnostic, ...]:
        """Validate the resolved model, language, voice, and quality."""
        from ..models import ModelDiscoveryError, get_model_info, language_matches
        from ..plan import (
            DIAG_MODEL_LANGUAGE_INCOMPATIBLE,
            DIAG_MODEL_NOT_FOUND,
            DIAG_QUALITY_UNAVAILABLE,
            DIAG_VOICE_UNAVAILABLE,
            PlanDiagnostic,
        )

        options = dict(selection.options)
        try:
            model, _result = get_model_info(
                selection.target_id,
                offline=bool(options.get("offline", False)),
                backend=self.id,
                refresh=bool(options.get("refresh", False)),
                preference=options.get("model_source", "auto"),
            )
        except ModelDiscoveryError as exc:
            return (
                PlanDiagnostic(
                    code=DIAG_MODEL_NOT_FOUND,
                    severity="error",
                    message=f"Model {selection.target_id!r} not found: {exc}",
                    field="render.target.id",
                ),
            )
        diagnostics: list[PlanDiagnostic] = []
        if not language_matches(selection.language, model.languages):
            diagnostics.append(
                PlanDiagnostic(
                    code=DIAG_MODEL_LANGUAGE_INCOMPATIBLE,
                    severity="error",
                    message=(
                        f"Model {selection.target_id!r} does not declare language "
                        f"{selection.language!r}."
                    ),
                    field="render.target.language",
                )
            )
        if selection.voice is not None and selection.voice not in model.voices:
            diagnostics.append(
                PlanDiagnostic(
                    code=DIAG_VOICE_UNAVAILABLE,
                    severity="error",
                    message=f"Voice {selection.voice!r} is not available for model {selection.target_id!r}.",
                    field="render.target.voice",
                )
            )
        quality = options.get("quality")
        if quality is not None and quality not in model.qualities:
            diagnostics.append(
                PlanDiagnostic(
                    code=DIAG_QUALITY_UNAVAILABLE,
                    severity="error",
                    message=f"Quality {quality!r} is not available for model {selection.target_id!r}.",
                    field="render.options.quality",
                )
            )
        return tuple(diagnostics)

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
            target_id=getattr(request, "target_id", None) or "default",
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
        from pykokoro import GenerationConfig, PipelineConfig
        from pykokoro.planning import planner_config_from_pipeline

        cfg = PipelineConfig(
            voice=selection.voice,
            generation=GenerationConfig(
                speed=float(selection.options.get("speed", 1.0)),
                lang=selection.language,
                pause_mode=str(selection.options.get("pause_mode", planning.pause_mode)),
            ),
        )
        return planner_config_from_pipeline(cfg, unit=planning.unit)

    def open(
        self,
        selection: EngineSelection,
    ) -> AbstractContextManager[PyKokoroEngineSession]:
        """Open a rendering session for the given selection."""
        from pykokoro import GenerationConfig, KokoroPipeline, PipelineConfig

        options = dict(selection.options)
        generation = GenerationConfig(
            speed=float(options.get("speed", selection.options.get("rate", 1.0))),
            lang=selection.language,
            pause_mode=str(options.get("pause_mode", "tts")),
        )
        config_kwargs: dict[str, Any] = {
            "voice": selection.voice,
            "model_variant": selection.target_id,
            "model_source": options.get("model_source"),
            "model_quality": options.get("quality"),
            "generation": generation,
            "allow_experimental_frontend": bool(options.get("allow_experimental", False)),
        }
        tokenizer_values = {
            "lexicons": options.get("lexicons"),
            "fallback": options.get("g2p_fallback"),
            "lexicon_data_policy": options.get("lexicon_data_policy"),
        }
        if options.get("spacy") not in {None, "auto"}:
            tokenizer_values["use_spacy"] = True
            tokenizer_values["spacy_model_size"] = options["spacy"]
        if any(value is not None for value in tokenizer_values.values()):
            from pykokoro.tokenizer import TokenizerConfig

            config_kwargs["tokenizer_config"] = TokenizerConfig(
                **{key: value for key, value in tokenizer_values.items() if value is not None}
            )
        short_sentence = options.get("short_sentence")
        if short_sentence not in {None, "auto"}:
            from pykokoro.short_sentence_handler import ShortSentenceConfig

            if short_sentence == "off":
                config_kwargs["short_sentence_config"] = ShortSentenceConfig(enabled=False)
            else:
                config_kwargs["short_sentence_config"] = ShortSentenceConfig(
                    enabled=True, resolve_mode=short_sentence
                )
        if options.get("ssmd_voice_bindings"):
            from pykokoro import SSMDRenderConfig

            config_kwargs["ssmd"] = SSMDRenderConfig(
                voice_bindings={"kokoro": dict(options["ssmd_voice_bindings"])}
            )
        if options.get("language_detection") is not None:
            from pykokoro import LanguageDetectionConfig

            config_kwargs["language_detection"] = LanguageDetectionConfig(
                mode=options["language_detection"],
                languages=tuple(options.get("detect_languages") or ()),
            )
        cfg = PipelineConfig(**config_kwargs)
        pipeline = KokoroPipeline(cfg)

        @contextmanager
        def _session() -> Any:
            try:
                yield PyKokoroEngineSession(pipeline)
            finally:
                pass  # Pipeline cleanup if needed

        return _session()


__all__ = ["PyKokoroEngineAdapter"]
