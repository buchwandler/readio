"""Request-centric adapter for the published PiperSynth voice API."""

from __future__ import annotations

import importlib.metadata
import inspect
import math
from collections.abc import Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from ..config import normalize_language_key
from ..errors import (
    EmptySpeechTextError,
    EngineBackendError,
    EngineSynthesisError,
    InvalidEngineLanguageError,
    InvalidEngineModelError,
    InvalidEngineOptionError,
    InvalidEngineSpeakerError,
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
    validate_rendered_speech,
)
from .catalog import CatalogRequest, SynthesisTarget

PIPER_RENDER_OPTIONS = frozenset(
    {
        "length_scale",
        "noise_scale",
        "noise_w_scale",
        "normalize_audio",
        "output_gain",
        "voice_level",
        "cache_dir",
        "providers",
        "provider_options",
        "session_options",
        "frontend_options",
        "force_download",
    }
)


def _target_from_voice_metadata(metadata: Any, engine: str = "piper") -> SynthesisTarget:
    speaker_map = dict(getattr(metadata, "speaker_id_map", {}) or {})
    language_code = getattr(metadata, "language_code", None)
    return SynthesisTarget(
        engine=engine,
        id=metadata.id,
        display_name=getattr(metadata, "name", None) or metadata.id,
        languages=(language_code,) if language_code else (),
        status="ready",
        runtime_available=True,
        sample_rate=getattr(metadata, "sample_rate", None),
        voices=(metadata.id,),
        speakers=tuple(str(name) for name in speaker_map),
        qualities=((metadata.quality,) if getattr(metadata, "quality", None) else ()),
        aliases=tuple(getattr(metadata, "aliases", ()) or ()),
        capabilities=frozenset({"speakers"}),
        metadata={
            "language_code": language_code,
            "languages": ((language_code,) if language_code else ()),
            "language_family": getattr(metadata, "language_family", None),
            "region": getattr(metadata, "region", None),
            "num_speakers": getattr(metadata, "num_speakers", 0),
            "speaker_id_map": speaker_map,
            "source_revision": getattr(metadata, "source_revision", None),
        },
    )


def _piper_version() -> str | None:
    try:
        return importlib.metadata.version("pipersynth")
    except importlib.metadata.PackageNotFoundError:
        return None


def _translate_piper_error(
    error: Exception,
    selection: EngineSelection,
    request: SpeechRequest | None = None,
    *,
    opening: bool = False,
) -> EngineSynthesisError:
    import pipersynth

    error_map = (
        ("SynthesisInputTooLongError", SpeechRequestTooLongError),
        ("EmptyTextError", EmptySpeechTextError),
        ("InvalidLanguageError", InvalidEngineLanguageError),
        ("InvalidSpeakerError", InvalidEngineSpeakerError),
        ("InvalidSynthesisConfigError", InvalidEngineOptionError),
        ("InvalidLinguisticTokensError", InvalidSpeechRequestError),
        ("InvalidPronunciationError", InvalidSpeechRequestError),
        ("InvalidRequestError", InvalidSpeechRequestError),
        ("UnsupportedFeatureError", UnsupportedSynthesisFeatureError),
        ("UnsupportedModelError", InvalidEngineModelError),
        ("VoiceNotFoundError", InvalidEngineModelError),
        ("ModelFileNotFoundError", InvalidEngineModelError),
        ("ConfigFileNotFoundError", InvalidEngineModelError),
        ("ModelInferenceError", EngineBackendError),
        ("VoiceClosedError", EngineBackendError),
    )
    error_type: type[EngineSynthesisError] = EngineBackendError
    for native_name, mapped_type in error_map:
        native_type = getattr(pipersynth, native_name, None)
        if isinstance(native_type, type) and isinstance(error, native_type):
            error_type = mapped_type
            break
    else:
        if opening and isinstance(error, (TypeError, ValueError)):
            error_type = InvalidEngineOptionError
        elif isinstance(error, (TypeError, ValueError)):
            error_type = InvalidSpeechRequestError

    context: dict[str, Any] = {
        "engine": "piper",
        "engine_version": _piper_version(),
        "target_id": selection.target_id,
        "language": request.language if request is not None else selection.language,
        "voice": request.voice if request is not None else selection.voice,
        "speaker": request.speaker if request is not None else selection.speaker,
        "request_id": request.id if request is not None else None,
        "native_error_type": type(error).__name__,
    }
    if error_type is SpeechRequestTooLongError:
        context.update(
            amount=getattr(error, "phoneme_count", None),
            maximum=getattr(error, "max_phonemes", None),
            unit="phoneme_ids",
            text_length=getattr(error, "text_length", len(request.text) if request else None),
            source="pipersynth.strict_error_fallback",
        )
    return error_type(str(error), **context)


def _piper_token(item: SpeechToken, module: Any) -> Any:
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


def _piper_pronunciation(item: PronunciationSpan, module: Any) -> Any:
    if item.alphabet not in (None, "ipa"):
        raise UnsupportedSynthesisFeatureError(
            f"PiperSynth supports IPA pronunciation overrides, not {item.alphabet!r}",
            engine="piper",
            language=item.language,
        )
    return module.PronunciationOverride(
        start=item.start,
        end=item.end,
        phonemes=item.phonemes,
        language=item.language,
    )


def _voice_level_metadata(value: Any, mode: str) -> dict[str, Any]:
    metadata = dict(value) if isinstance(value, Mapping) else {}
    return {
        "mode": metadata.get("mode", mode),
        "applied": bool(metadata.get("applied", False)),
        "gain_db": metadata.get("gain_db"),
        "source": metadata.get("source", "none"),
        "reason": metadata.get("reason"),
        "calibration_identity": metadata.get(
            "calibration_key", metadata.get("calibration_identity")
        ),
        "calibration_revision": metadata.get(
            "catalog_revision", metadata.get("calibration_revision")
        ),
    }


class PiperSynthEngineSession:
    """Adapt one exact Readio request to one open PiperVoice."""

    def __init__(self, voice: Any, config: Any, selection: EngineSelection | None = None) -> None:
        self._voice = voice
        self._config = config
        self._selection = selection or EngineSelection(
            engine="piper", target_id="unknown", language="und"
        )

    def measure(self, request: SpeechRequest) -> RequestMeasure:
        return RequestMeasure(
            fits=None,
            amount=None,
            maximum=None,
            unit="phoneme_ids",
            source="pipersynth.strict_error_fallback",
        )

    def synthesize(self, request: SpeechRequest) -> RenderedSpeech:
        import pipersynth

        try:
            native = pipersynth.SynthesisRequest(
                id=request.id,
                text=request.text,
                language=request.language,
                speaker=request.speaker,
                tokens=tuple(_piper_token(token, pipersynth) for token in request.tokens),
                pronunciation_overrides=tuple(
                    _piper_pronunciation(span, pipersynth)
                    for span in request.pronunciation_overrides
                ),
            )
            result = self._voice.synthesize(native, config=self._config)
        except EngineSynthesisError:
            raise
        except Exception as exc:
            raise _translate_piper_error(exc, self._selection, request) from exc

        metadata = dict(result.metadata)
        metadata["voice_level"] = _voice_level_metadata(
            metadata.get("voice_level"), str(self._selection.options.get("voice_level", "off"))
        )
        rendered = RenderedSpeech(
            id=result.id,
            audio=result.audio,
            sample_rate=result.sample_rate,
            warnings=tuple(result.warnings),
            word_timings=(),
            metadata=metadata,
        )
        return validate_rendered_speech(
            request,
            rendered,
            engine="piper",
            engine_version=_piper_version(),
            target_id=self._selection.target_id,
        )


class PiperSynthEngineAdapter:
    """PiperSynth implementation of Readio's request-oriented engine contract."""

    id = "piper"
    package_name = "pipersynth"

    def version(self) -> str | None:
        try:
            return importlib.metadata.version(self.package_name)
        except importlib.metadata.PackageNotFoundError:
            return None

    def compatible_api(self) -> bool:
        try:
            import pipersynth

            required = (
                "PiperVoice",
                "SynthesisRequest",
                "SynthesisResult",
                "SynthesisConfig",
                "LinguisticToken",
                "PronunciationOverride",
                "VoiceLevelConfig",
                "SynthesisInputTooLongError",
            )
            if not all(hasattr(pipersynth, name) for name in required):
                return False
            parameters = inspect.signature(pipersynth.PiperVoice.synthesize).parameters
            return (
                "request" in parameters
                and "config" in parameters
                and parameters["config"].kind is inspect.Parameter.KEYWORD_ONLY
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
            voice_binding_namespace="piper",
            voice_binding_scope="target",
            option_names=PIPER_RENDER_OPTIONS,
            supports_speakers=True,
            supports_linguistic_tokens=True,
            supports_pronunciation_overrides=True,
            pronunciation_alphabets=frozenset({"ipa"}),
            supports_voice_level_calibration=True,
            supports_request_measurement=False,
            supports_qualities=True,
            supports_live=True,
            supports_timestamps=False,
        )

    def discover(self, request: CatalogRequest) -> tuple[SynthesisTarget, ...]:
        try:
            from pipersynth.asset_manager import VoiceAssetManager
        except ImportError:
            return ()
        manager = VoiceAssetManager(offline=request.offline)
        voices = manager.list_voices(language=request.language, refresh=request.refresh)
        return tuple(_target_from_voice_metadata(item, self.id) for item in voices)

    def resolve(self, request: Any) -> tuple[EngineSelection, tuple[Any, ...]]:
        language = normalize_language_key(getattr(request, "language", None) or "en-us")
        voice = getattr(request, "voice", None)
        target_id = getattr(request, "target_id", None) or voice
        if not target_id:
            raise ValueError(
                "piper.voice_required: Piper requires a voice bundle; pass --model <id> or "
                f"run `readio voices list --engine piper --lang {language}`."
            )
        options = dict(getattr(request, "options", {}) or {})
        options.update(dict(getattr(request, "engine_options", {}) or {}))
        speed = options.pop("speed", options.pop("rate", None))
        if speed is not None:
            try:
                speed = float(speed)
            except (TypeError, ValueError) as exc:
                raise ValueError("piper.invalid_speed: speed must be finite and > 0") from exc
            if not math.isfinite(speed) or speed <= 0:
                raise ValueError("piper.invalid_speed: speed must be finite and > 0")
            options.setdefault("length_scale", 1.0 / speed)
        if options.get("lexicons"):
            raise ValueError("piper.option_not_supported: PiperSynth does not support lexicons")
        engine_options = {
            key: value for key, value in options.items() if key in PIPER_RENDER_OPTIONS
        }
        return (
            EngineSelection(
                engine=self.id,
                target_id=target_id,
                language=language,
                voice=voice or target_id,
                speaker=getattr(request, "speaker", None),
                options=engine_options,
                offline=bool(getattr(request, "offline", False)),
                refresh=bool(getattr(request, "refresh", False)),
            ),
            (),
        )

    def validate_selection(self, selection: EngineSelection) -> tuple[Any, ...]:
        from ..plan import PlanDiagnostic

        try:
            from pipersynth.asset_manager import VoiceAssetManager
        except ImportError:
            return (
                PlanDiagnostic(
                    code="piper.engine_unavailable",
                    severity="error",
                    message='PiperSynth is not installed. Install with: pip install "readio[piper]"',
                    field="synthesis.engine",
                ),
            )
        manager = VoiceAssetManager(offline=selection.offline)
        try:
            metadata = manager.get_voice_metadata(selection.target_id, refresh=selection.refresh)
        except (KeyError, LookupError, OSError, ValueError) as exc:
            return (
                PlanDiagnostic(
                    code="piper.voice_not_found",
                    severity="error",
                    message=f"Piper voice bundle {selection.target_id!r} was not found: {exc}",
                    field="render.target.id",
                ),
            )
        requested = normalize_language_key(selection.language)
        actual = normalize_language_key(metadata.language_code)
        if requested != actual and requested.split("-", 1)[0] != actual.split("-", 1)[0]:
            return (
                PlanDiagnostic(
                    code="piper.language_mismatch",
                    severity="error",
                    message=f"Piper voice {selection.target_id!r} is {actual}, not {requested}.",
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
                        message=f"Speaker {selection.speaker!r} is not available in {selection.target_id!r}.",
                        field="render.target.speaker",
                    ),
                )
        return ()

    def target_metadata(self, selection: EngineSelection) -> Mapping[str, Any]:
        try:
            from pipersynth.asset_manager import VoiceAssetManager

            metadata = VoiceAssetManager(offline=selection.offline).get_voice_metadata(
                selection.target_id,
                refresh=selection.refresh,
            )
        except (ImportError, KeyError, LookupError, OSError, ValueError):
            return {}
        language = metadata.language_code
        return {
            "language_code": language,
            "languages": (language,) if language else (),
            "language_family": metadata.language_family,
            "region": metadata.region,
            "num_speakers": metadata.num_speakers,
            "speaker_id_map": dict(metadata.speaker_id_map),
            "source_revision": metadata.source_revision,
            "quality": metadata.quality,
            "sample_rate": getattr(metadata, "sample_rate", None),
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

    def open(self, selection: EngineSelection) -> AbstractContextManager[PiperSynthEngineSession]:
        import pipersynth

        options = dict(selection.options)
        try:
            voice = pipersynth.PiperVoice.from_pretrained(
                selection.target_id,
                cache_dir=options.get("cache_dir"),
                offline=selection.offline,
                refresh_catalog=selection.refresh,
                force_download=bool(options.get("force_download", False)),
                providers=options.get("providers"),
                provider_options=options.get("provider_options"),
                session_options=options.get("session_options"),
                frontend_options=options.get("frontend_options"),
            )
            config = pipersynth.SynthesisConfig(
                length_scale=options.get("length_scale"),
                noise_scale=options.get("noise_scale"),
                noise_w_scale=options.get("noise_w_scale"),
                normalize_audio=bool(options.get("normalize_audio", True)),
                output_gain=float(options.get("output_gain", 1.0)),
                voice_level=pipersynth.VoiceLevelConfig(mode=options.get("voice_level", "off")),
            )
        except Exception as exc:
            raise _translate_piper_error(exc, selection, opening=True) from exc

        @contextmanager
        def session() -> Any:
            try:
                yield PiperSynthEngineSession(voice, config, selection)
            finally:
                voice.close()

        return session()


__all__ = ["PiperSynthEngineAdapter", "PiperSynthEngineSession"]
