"""Request-centric PyKokoro adapter."""

from __future__ import annotations

import importlib.metadata
import logging
from collections.abc import Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, is_dataclass
from typing import Any

from ..errors import (
    EmptySpeechTextError,
    EngineBackendError,
    EngineSynthesisError,
    InvalidEngineLanguageError,
    InvalidEngineModelError,
    InvalidEngineOptionError,
    InvalidEngineVoiceError,
    InvalidSpeechRequestError,
    SpeechRequestTooLongError,
    UnsupportedSynthesisFeatureError,
)
from .base import (
    EngineCapabilities,
    EngineSelection,
    PronunciationSpan,
    RenderedSpeech,
    RequestMeasure,
    SpeechRequest,
    SpeechToken,
    SpeechWordTiming,
    validate_rendered_speech,
)
from .catalog import SynthesisTarget

logger = logging.getLogger(__name__)


def _pykokoro_version() -> str | None:
    try:
        return importlib.metadata.version("pykokoro")
    except importlib.metadata.PackageNotFoundError:
        return None


def _translate_pykokoro_error(
    error: Exception,
    selection: EngineSelection,
    request: SpeechRequest | None = None,
    *,
    opening: bool = False,
) -> EngineSynthesisError:
    import pykokoro

    error_map = (
        ("SynthesisInputTooLongError", SpeechRequestTooLongError),
        ("EmptyTextError", EmptySpeechTextError),
        ("InvalidModelError", InvalidEngineModelError),
        ("InvalidVoiceError", InvalidEngineVoiceError),
        ("InvalidLanguageError", InvalidEngineLanguageError),
        ("InvalidPronunciationError", InvalidSpeechRequestError),
        ("InvalidLinguisticTokensError", InvalidSpeechRequestError),
        ("ConfigurationError", InvalidEngineOptionError),
        ("UnsupportedFeatureError", UnsupportedSynthesisFeatureError),
        ("BackendError", EngineBackendError),
        ("AlignmentError", EngineBackendError),
        ("SynthesisStateError", EngineBackendError),
    )
    error_type: type[EngineSynthesisError] = EngineBackendError
    for native_name, mapped_type in error_map:
        native_type = getattr(pykokoro, native_name, None)
        if isinstance(native_type, type) and isinstance(error, native_type):
            error_type = mapped_type
            break
    else:
        if opening and isinstance(error, (TypeError, ValueError)):
            error_type = InvalidEngineOptionError
        elif isinstance(error, (TypeError, ValueError)):
            error_type = InvalidSpeechRequestError

    context: dict[str, Any] = {
        "engine": "pykokoro",
        "engine_version": _pykokoro_version(),
        "target_id": selection.target_id,
        "language": request.language if request is not None else selection.language,
        "voice": request.voice if request is not None else selection.voice,
        "speaker": request.speaker if request is not None else selection.speaker,
        "request_id": request.id if request is not None else None,
        "native_error_type": type(error).__name__,
    }
    if error_type is SpeechRequestTooLongError:
        context.update(
            amount=getattr(error, "token_count", None),
            maximum=getattr(error, "max_tokens", None),
            unit="model_tokens",
            text_length=getattr(error, "text_length", len(request.text) if request else None),
            source="pykokoro.prepare",
        )
    return error_type(str(error), **context)


def _pykokoro_request(request: SpeechRequest, module: Any) -> Any:
    return module.SynthesisRequest(
        id=request.id,
        text=request.text,
        language=request.language,
        voice=request.voice,
        pronunciation_overrides=tuple(
            _pykokoro_override(item, module) for item in request.pronunciation_overrides
        ),
        tokens=tuple(_pykokoro_token(item, module) for item in request.tokens),
        phonemes=request.whole_request_phonemes,
    )


def _voice_level_metadata(value: Any, mode: str) -> dict[str, Any]:
    if is_dataclass(value) and not isinstance(value, type):
        metadata = asdict(value)
    elif isinstance(value, Mapping):
        metadata = dict(value)
    else:
        metadata = {}
    return {
        "mode": metadata.get("mode", mode),
        "applied": bool(metadata.get("applied", False)),
        "gain_db": metadata.get("gain_db"),
        "source": metadata.get("source", "none"),
        "reason": metadata.get("reason"),
        "calibration_identity": metadata.get("key", metadata.get("calibration_identity")),
        "calibration_revision": metadata.get("corpus", metadata.get("catalog_revision")),
    }


class PyKokoroEngineSession:
    """Adapt neutral requests to one open KokoroSynthesizer."""

    def __init__(self, synthesizer: Any, selection: EngineSelection | None = None) -> None:
        self._synthesizer = synthesizer
        self._selection = selection or EngineSelection(
            engine="pykokoro", target_id="unknown", language="und"
        )

    def measure(self, request: SpeechRequest) -> RequestMeasure:
        import pykokoro

        try:
            prepared = self._synthesizer.prepare(_pykokoro_request(request, pykokoro))
        except EngineSynthesisError:
            raise
        except Exception as exc:
            raise _translate_pykokoro_error(exc, self._selection, request) from exc
        token_ids = tuple(getattr(prepared, "token_ids", ()))
        amount = len(token_ids)
        maximum = self._selection.metadata.get("max_tokens")
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0:
            maximum = None
        return RequestMeasure(
            fits=amount <= maximum if maximum is not None else None,
            amount=amount,
            maximum=maximum,
            unit="model_tokens",
            source="pykokoro.prepare",
        )

    def synthesize(self, request: SpeechRequest) -> RenderedSpeech:
        import pykokoro

        try:
            result = self._synthesizer.synthesize(_pykokoro_request(request, pykokoro))
        except EngineSynthesisError:
            raise
        except Exception as exc:
            raise _translate_pykokoro_error(exc, self._selection, request) from exc

        timings = tuple(
            SpeechWordTiming(
                text=item.text,
                char_start=item.char_start,
                char_end=item.char_end,
                start_sample=item.start_sample,
                end_sample=item.end_sample,
            )
            for item in result.word_timings
        )
        applications = tuple(getattr(result, "voice_level_applications", ()) or ())
        metadata: dict[str, Any] = {
            "language": result.language,
            "voice": result.voice,
            "phonemes": result.phonemes,
            "token_ids": tuple(result.token_ids),
            "voice_level": _voice_level_metadata(
                applications[0] if applications else None,
                str(self._selection.options.get("voice_level", "off")),
            ),
        }
        if result.trace is not None:
            metadata["trace"] = result.trace
        rendered = RenderedSpeech(
            id=result.id,
            audio=result.audio,
            sample_rate=result.sample_rate,
            warnings=tuple(result.diagnostics),
            word_timings=timings,
            metadata=metadata,
        )
        return validate_rendered_speech(
            request,
            rendered,
            engine="pykokoro",
            engine_version=_pykokoro_version(),
            target_id=self._selection.target_id,
        )


def _pykokoro_override(item: PronunciationSpan, module: Any) -> Any:
    return module.PronunciationOverride(
        start=item.start,
        end=item.end,
        phonemes=item.phonemes,
        language=item.language,
    )


def _pykokoro_token(item: SpeechToken, module: Any) -> Any:
    return module.LinguisticToken(
        start=item.start,
        end=item.end,
        text=item.text,
        pos=item.pos,
        tag=item.tag,
        lemma=item.lemma,
        language=item.language,
        morph=item.morph,
    )


class PyKokoroEngineAdapter:
    """PyKokoro implementation of Readio's request-oriented engine contract."""

    id = "pykokoro"
    package_name = "pykokoro"

    def version(self) -> str | None:
        try:
            return importlib.metadata.version(self.package_name)
        except importlib.metadata.PackageNotFoundError:
            return None

    def compatible_api(self) -> bool:
        try:
            import pykokoro

            required = (
                "KokoroSynthesizer",
                "SynthesisConfig",
                "SynthesisRequest",
                "PronunciationOverride",
                "LinguisticToken",
                "VoiceLevelConfig",
                "SynthesisInputTooLongError",
                "ShortSentenceConfig",
            )
            if not all(hasattr(pykokoro, name) for name in required):
                return False
            synthesizer = pykokoro.KokoroSynthesizer
            return callable(getattr(synthesizer, "prepare", None)) and callable(
                getattr(synthesizer, "synthesize", None)
            )
        except (
            ImportError,
            SyntaxError,
            OSError,
            RuntimeError,
            AttributeError,
            TypeError,
            ValueError,
        ):
            return False

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            id=self.id,
            voice_binding_namespace="kokoro",
            option_names=frozenset(
                {
                    "lexicons",
                    "model_source",
                    "quality",
                    "speed",
                    "random_seed",
                    "voice_level",
                    "short_sentence",
                    "g2p_fallback",
                    "lexicon_data_policy",
                    "waveform_validation",
                    "trace",
                    "allow_experimental_frontend",
                }
            ),
            supports_named_voices=True,
            supports_pronunciation_overrides=True,
            pronunciation_alphabets=frozenset({"ipa"}),
            supports_linguistic_tokens=True,
            supports_whole_request_phonemes=True,
            supports_lexicons=True,
            supports_model_sources=True,
            supports_qualities=True,
            supports_live=True,
            supports_timestamps=True,
            supports_voice_level_calibration=True,
            supports_request_measurement=True,
        )

    def discover(self, request: Any) -> tuple[SynthesisTarget, ...]:
        from ..models import _discover_pykokoro_model_info

        models, _result = _discover_pykokoro_model_info(
            language=getattr(request, "language", None),
            offline=getattr(request, "offline", False),
            preference=getattr(request, "preference", "auto"),
            refresh=getattr(request, "refresh", False),
        )
        return tuple(
            SynthesisTarget(
                engine=self.id,
                id=model.id,
                display_name=model.id,
                languages=model.languages,
                status=model.status,
                runtime_available=model.runtime_available,
                voices=model.voices,
                qualities=model.qualities,
                aliases=getattr(model, "aliases", ()),
                capabilities=frozenset({"named_voices", "pronunciation_overrides", "lexicons"}),
                metadata={
                    "source": model.source,
                    "g2p_backend": model.g2p_backend,
                    "frontend": model.frontend,
                    "default_voice": model.default_voice,
                    "lexicons": model.lexicons,
                    "experimental": model.experimental,
                    "redistribution_allowed": model.redistribution_allowed,
                    "distribution_id": model.distribution_id,
                    "provider": model.provider,
                    "distribution_provider": model.distribution_provider,
                    "sample_rate": model.sample_rate,
                    "max_tokens": model.max_tokens,
                    "voice_details": [
                        {
                            "id": item.id,
                            "gender": item.gender,
                            "language": item.language,
                            "locale": item.locale,
                            "language_label": item.language_label,
                        }
                        for item in model.voice_details
                    ],
                },
            )
            for model in models
        )

    def discover_lexicons(
        self,
        *,
        language: str | None = None,
        model: str | None = None,
        offline: bool = False,
        refresh: bool = False,
        preference: str = "auto",
    ) -> tuple[tuple[Any, ...], Any]:
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

    def resolve(self, request: Any) -> tuple[EngineSelection, tuple[Any, ...]]:
        options = dict(getattr(request, "options", {}) or {})
        options.update(dict(getattr(request, "engine_options", {}) or {}))
        return (
            EngineSelection(
                engine=self.id,
                target_id=getattr(request, "target_id", None)
                or options.get("model_variant")
                or "v1.0",
                language=getattr(request, "language", "en-us"),
                voice=getattr(request, "voice", None),
                speaker=getattr(request, "speaker", None),
                options=options,
                offline=bool(getattr(request, "offline", False)),
                refresh=bool(getattr(request, "refresh", False)),
            ),
            (),
        )

    def validate_selection(self, selection: EngineSelection) -> tuple[Any, ...]:
        from ..models import ModelDiscoveryError, get_model_info, language_matches
        from ..plan import PlanDiagnostic

        try:
            model, _ = get_model_info(
                selection.target_id,
                offline=selection.offline,
                engine=self.id,
                refresh=selection.refresh,
                preference=selection.options.get("model_source", "auto"),
            )
        except (ModelDiscoveryError, ImportError, OSError, ValueError) as exc:
            return (
                PlanDiagnostic(
                    code="pykokoro.model_not_found",
                    severity="error",
                    message=f"PyKokoro target {selection.target_id!r} was not found: {exc}",
                    field="render.target.id",
                ),
            )
        diagnostics: list[Any] = []
        if not language_matches(selection.language, model.languages):
            diagnostics.append(
                PlanDiagnostic(
                    code="pykokoro.language_incompatible",
                    severity="error",
                    message=f"Target {selection.target_id!r} does not support {selection.language!r}.",
                    field="render.target.language",
                )
            )
        if selection.voice is not None and selection.voice not in model.voices:
            diagnostics.append(
                PlanDiagnostic(
                    code="pykokoro.voice_unavailable",
                    severity="error",
                    message=f"Voice {selection.voice!r} is not available on {selection.target_id!r}.",
                    field="render.target.voice",
                )
            )
        quality = selection.options.get("quality")
        if quality is not None and quality not in model.qualities:
            diagnostics.append(
                PlanDiagnostic(
                    code="pykokoro.quality_unavailable",
                    severity="error",
                    message=f"Quality {quality!r} is not available on {selection.target_id!r}.",
                    field="render.options.quality",
                )
            )
        return tuple(diagnostics)

    def target_metadata(self, selection: EngineSelection) -> Mapping[str, Any]:
        from ..models import get_model_info

        try:
            model, _ = get_model_info(
                selection.target_id,
                offline=selection.offline,
                engine=self.id,
                refresh=selection.refresh,
                preference=selection.options.get("model_source", "auto"),
            )
        except (ImportError, OSError, ValueError):
            return {}
        return {
            "languages": tuple(model.languages),
            "voices": tuple(model.voices),
            "sample_rate": model.sample_rate,
            "max_tokens": model.max_tokens,
            "model_source": model.source,
            "model_identity": model.distribution_id or model.id,
            "distribution_id": model.distribution_id,
        }

    def canonical_synthesis_identity(self, selection: EngineSelection) -> Mapping[str, Any]:
        return {
            "engine": self.id,
            "engine_version": self.version(),
            "target_id": selection.target_id,
            "voice": selection.voice,
            "speaker": selection.speaker,
            "options": dict(selection.options),
            "metadata": dict(selection.metadata),
        }

    def open(self, selection: EngineSelection) -> AbstractContextManager[PyKokoroEngineSession]:
        import pykokoro

        options = dict(selection.options)
        try:
            generation = pykokoro.GenerationConfig(
                speed=float(options.get("speed", 1.0)),
                lang=selection.language,
                random_seed=options.get("random_seed"),
            )
            tokenizer = pykokoro.TokenizerConfig(
                lexicons=options.get("lexicons"),
                fallback=options.get("g2p_fallback", "espeak"),
                lexicon_data_policy=options.get("lexicon_data_policy", "auto"),
            )
            short_sentence = options.get("short_sentence")
            short_sentence_config = None
            if short_sentence is not None:
                short_sentence_config = pykokoro.ShortSentenceConfig(
                    enabled=short_sentence != "off",
                    **({"resolve_mode": short_sentence} if short_sentence != "off" else {}),
                )
            config = pykokoro.SynthesisConfig(
                voice=selection.voice,
                generation=generation,
                model_quality=options.get("quality"),
                model_source=options.get("model_source"),
                model_variant=selection.target_id,
                tokenizer_config=tokenizer,
                short_sentence_config=short_sentence_config,
                waveform_validation=options.get("waveform_validation", "off"),
                return_trace=bool(options.get("trace", False)),
                allow_experimental_frontend=bool(options.get("allow_experimental_frontend", False)),
                voice_level=pykokoro.VoiceLevelConfig(mode=options.get("voice_level", "off")),
                long_text_split="none",
                long_text_use_spacy=False,
            )
            synthesizer = pykokoro.KokoroSynthesizer(config)
        except Exception as exc:
            raise _translate_pykokoro_error(exc, selection, opening=True) from exc

        @contextmanager
        def session() -> Any:
            try:
                yield PyKokoroEngineSession(synthesizer, selection)
            finally:
                synthesizer.close()

        return session()


__all__ = ["PyKokoroEngineAdapter", "PyKokoroEngineSession"]
