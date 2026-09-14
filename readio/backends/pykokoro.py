"""PyKokoro implementation of Readio's synthesis backend contract."""

from __future__ import annotations

import importlib.metadata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from .base import BackendResolution, DiscoveryInfo

if TYPE_CHECKING:
    from ..config import ReadioConfig
    from ..document import InputDocument
    from ..lexicons import LexiconCatalogEntry
    from ..models import ModelInfo
    from ..plan import PlanDiagnostic, ReadioPlan, SynthesisCandidate
    from ..synthesis import ResolvedSynthesis


class PyKokoroBackend:
    """Adapter for PyKokoro discovery, configuration, and execution."""

    id = "pykokoro"
    ssmd_provider = "kokoro"
    supported_options = frozenset(
        {
            "model_source",
            "g2p_fallback",
            "lexicon_data_policy",
            "spacy",
            "short_sentence",
            "language_detection",
            "detect_language",
        }
    )

    def version(self) -> str | None:
        try:
            return importlib.metadata.version("pykokoro")
        except importlib.metadata.PackageNotFoundError:
            return None

    def discover_models(
        self,
        *,
        language: str | None = None,
        status: str | None = None,
        offline: bool = False,
        refresh: bool = False,
        preference: str = "auto",
    ) -> tuple[tuple[ModelInfo, ...], DiscoveryInfo]:
        from ..models import _discover_pykokoro_model_info

        models, result = _discover_pykokoro_model_info(
            language=language,
            status=status,
            offline=offline,
            refresh=refresh,
            preference=preference,
        )
        return models, DiscoveryInfo(
            registry_source=getattr(result, "registry_source", None),
            cache_fallback=bool(getattr(result, "cache_fallback", False)),
            offline=bool(getattr(result, "offline", offline)),
            refreshed=bool(getattr(result, "refreshed", refresh)),
        )

    def discover_lexicons(
        self,
        *,
        language: str | None = None,
        model: str | None = None,
        offline: bool = False,
        refresh: bool = False,
        preference: str = "auto",
    ) -> tuple[tuple[LexiconCatalogEntry, ...], DiscoveryInfo]:
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
        return entries, DiscoveryInfo(
            registry_source=result.registry_source,
            cache_fallback=result.cache_fallback,
            offline=result.offline,
            refreshed=result.refreshed,
        )

    def resolve_defaults(
        self, candidate: SynthesisCandidate
    ) -> tuple[SynthesisCandidate, tuple[PlanDiagnostic, ...]]:
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

    def validate_selection(self, selection: BackendResolution) -> tuple[PlanDiagnostic, ...]:
        return ()

    @staticmethod
    def _spacy_settings(policy: str | None) -> tuple[bool | None, str | None]:
        if policy in {None, "auto"}:
            return None, None
        if policy == "off":
            return False, None
        if policy == "required":
            policy = "sm"
        if policy in {"sm", "md", "lg", "trf"}:
            return True, policy
        raise ValueError(f"unsupported spaCy policy: {policy!r}")

    def tokenizer_config_for_synthesis(self, synthesis: object) -> Any:
        from pykokoro.tokenizer import TokenizerConfig

        use_spacy, spacy_model_size = self._spacy_settings(getattr(synthesis, "spacy", None))
        values = {
            "lexicons": getattr(synthesis, "lexicons", None),
            "fallback": getattr(synthesis, "g2p_fallback", None),
            "lexicon_data_policy": getattr(synthesis, "lexicon_data_policy", None),
            "use_spacy": use_spacy,
            "spacy_model_size": spacy_model_size,
        }
        if not any(value is not None for value in values.values()):
            return None
        return TokenizerConfig(**{key: value for key, value in values.items() if value is not None})

    def short_sentence_config_for_synthesis(self, synthesis: object) -> Any:
        policy = getattr(synthesis, "short_sentence", None)
        if policy in {None, "auto"}:
            return None
        from pykokoro.short_sentence_handler import ShortSentenceConfig

        if policy == "off":
            return ShortSentenceConfig(enabled=False)
        if policy in {"wrap", "phrase", "randomized-phrase"}:
            return ShortSentenceConfig(enabled=True, resolve_mode=policy)
        raise ValueError(f"unsupported short-sentence policy: {policy!r}")

    def language_detection_config_for_synthesis(
        self, synthesis: object, document: InputDocument | None = None
    ) -> Any:
        mode = getattr(synthesis, "language_detection", None)
        languages = getattr(synthesis, "detect_languages", None)
        if mode is None and document is not None and document.format == "ssmd":
            from ..ssmd import language_detection_hint

            hint = language_detection_hint(document.text)
            if hint is not None:
                mode, languages = hint
        if mode is None:
            return None
        from pykokoro import LanguageDetectionConfig

        return LanguageDetectionConfig(mode=mode, languages=tuple(languages or ()))

    def pipeline_config_for_document(
        self,
        document: InputDocument,
        cfg: ReadioConfig,
        *,
        ssmd_voice_bindings: dict[str, str] | None = None,
        synthesis: ResolvedSynthesis | None = None,
    ) -> Any:
        from pykokoro import GenerationConfig, PipelineConfig, SSMDRenderConfig

        from ..ssmd import build_ssmd_render_config
        from ..synthesis import resolve_synthesis

        resolved = synthesis or resolve_synthesis(cfg)
        generation = GenerationConfig(
            lang=resolved.language,
            speed=resolved.speed,
            pause_mode=resolved.pause_mode,
        )
        ssmd = (
            build_ssmd_render_config(document.text, cfg, ssmd_voice_bindings, resolved)
            if document.format == "ssmd"
            else SSMDRenderConfig()
        )
        return PipelineConfig(
            voice=resolved.voice,
            model_source=resolved.source,
            model_variant=resolved.model,
            model_quality=resolved.quality,
            allow_experimental_frontend=resolved.allow_experimental,
            generation=generation,
            language_detection=self.language_detection_config_for_synthesis(resolved, document),
            tokenizer_config=self.tokenizer_config_for_synthesis(resolved),
            short_sentence_config=self.short_sentence_config_for_synthesis(resolved),
            ssmd=ssmd,
        )

    def build_ssmd_render_config(
        self,
        text: str,
        cfg: ReadioConfig,
        additional_bindings: dict[str, str] | None = None,
        synthesis: ResolvedSynthesis | None = None,
    ) -> Any:
        from pykokoro import SSMDRenderConfig

        from ..ssmd import default_role_bindings

        return SSMDRenderConfig(
            provider=cfg.ssmd.voice_provider,
            voice_bindings=default_role_bindings(text, cfg, additional_bindings, synthesis),
            missing_voice="error",
        )

    def pipeline_config_from_plan(self, plan: ReadioPlan, document: InputDocument) -> Any:
        from pykokoro import GenerationConfig, PipelineConfig, SSMDRenderConfig

        synthesis = plan.synthesis
        if synthesis is None:
            raise ValueError("plan has no synthesis; cannot build pipeline config")
        generation = GenerationConfig(
            lang=synthesis.language,
            speed=synthesis.speed,
            pause_mode=synthesis.pause_mode,
        )
        if document.format == "ssmd" and plan.ssmd.enabled:
            provider = plan.ssmd.provider or self.ssmd_provider
            bindings_map: dict[str, dict[str, str]] = {}
            if plan.ssmd.bindings:
                bindings_map[provider] = {
                    binding.reference: binding.voice for binding in plan.ssmd.bindings
                }
            ssmd = SSMDRenderConfig(
                provider=provider,
                voice_bindings=bindings_map,
                missing_voice="error",
            )
        else:
            ssmd = SSMDRenderConfig()
        model = synthesis.model
        return PipelineConfig(
            voice=model.voice,
            model_source=model.source,
            model_variant=model.id,
            model_quality=model.quality,
            allow_experimental_frontend=synthesis.allow_experimental,
            generation=generation,
            language_detection=self.language_detection_config_for_synthesis(synthesis),
            tokenizer_config=self.tokenizer_config_for_synthesis(synthesis),
            ssmd=ssmd,
            short_sentence_config=self.short_sentence_config_for_synthesis(synthesis),
        )

    @contextmanager
    def open_session(self, plan: ReadioPlan, document: InputDocument) -> Iterator[Any]:
        from pykokoro import KokoroPipeline

        with KokoroPipeline(self.pipeline_config_from_plan(plan, document)) as pipeline:
            yield pipeline

    def open_resolved_session(
        self,
        document: InputDocument,
        cfg: ReadioConfig,
        *,
        ssmd_voice_bindings: dict[str, str] | None = None,
        synthesis: ResolvedSynthesis | None = None,
    ) -> Any:
        from pykokoro import KokoroPipeline

        from ..ssmd import preflight_ssmd
        from ..synthesis import resolve_synthesis

        resolved = synthesis or resolve_synthesis(cfg)
        if document.format == "ssmd" and cfg.ssmd.validate_before_render:
            preflight_ssmd(
                document.text,
                cfg,
                source_path=document.source_path,
                additional_bindings=ssmd_voice_bindings,
                synthesis=resolved,
            )
        return KokoroPipeline(
            self.pipeline_config_for_document(
                document,
                cfg,
                ssmd_voice_bindings=ssmd_voice_bindings,
                synthesis=resolved,
            )
        )

    def create_playback_player(self, sample_rate: int, settings: object, channels: int) -> Any:
        from pykokoro.playback import SoundDevicePlayer

        return SoundDevicePlayer(
            sample_rate,
            device=settings.device,
            queue_size=settings.queue_size,
            channels=channels,
        )

    def open_legacy_session(self, document: InputDocument, settings: object) -> Any:
        from pykokoro import GenerationConfig, KokoroPipeline, PipelineConfig

        return KokoroPipeline(
            PipelineConfig(
                voice=settings.voice,
                generation=GenerationConfig(
                    lang=settings.lang,
                    speed=settings.speed,
                    pause_mode=settings.pause_mode,
                ),
                tokenizer_config=self.tokenizer_config_for_synthesis(settings),
                short_sentence_config=self.short_sentence_config_for_synthesis(settings),
            )
        )


__all__ = ["PyKokoroBackend"]
