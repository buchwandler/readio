"""Request-centric adapter for the published PiperSynth voice API."""

from __future__ import annotations

import importlib.metadata
import inspect
import math
from collections.abc import Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import replace
from typing import Any

from ..config import normalize_language_key
from .base import EngineCapabilities, EngineSelection, RenderedSpeech, SpeechRequest
from .catalog import CatalogRequest, SynthesisTarget

PIPER_RENDER_OPTIONS = frozenset(
    {
        "length_scale",
        "noise_scale",
        "noise_w_scale",
        "normalize_audio",
        "volume",
        "loudness",
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


class PiperSynthEngineSession:
    """Adapt neutral text requests to one open PiperVoice."""

    def __init__(self, voice: Any, config: Any) -> None:
        self._voice = voice
        self._config = config

    def synthesize(self, request: SpeechRequest) -> RenderedSpeech:
        import numpy as np

        config = replace(
            self._config,
            speaker_id=self._voice.resolve_speaker_id(request.speaker),
        )
        chunks = tuple(self._voice.synthesize(request.text, config))
        sample_rate = chunks[0].sample_rate if chunks else self._voice.config.sample_rate
        if any(chunk.sample_rate != sample_rate for chunk in chunks):
            raise ValueError(
                "piper.sample_rate_mismatch: sentence chunks use different sample rates"
            )
        audio_parts = [np.asarray(chunk.audio_float_array, dtype=np.float32) for chunk in chunks]
        audio = np.concatenate(audio_parts) if audio_parts else np.zeros(0, dtype=np.float32)
        if audio.ndim != 1 or not np.isfinite(audio).all():
            raise ValueError("piper.rendered_audio_invalid: expected finite mono float32 audio")
        return RenderedSpeech(
            id=request.id,
            audio=audio,
            sample_rate=sample_rate,
            warnings=tuple(warning for chunk in chunks for warning in chunk.warnings),
            metadata={
                "phonemes": tuple(phoneme for chunk in chunks for phoneme in chunk.phonemes),
                "phoneme_ids": tuple(
                    identifier for chunk in chunks for identifier in chunk.phoneme_ids
                ),
                "chunks": len(chunks),
            },
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

            voice = getattr(pipersynth, "PiperVoice", None)
            synthesize = getattr(voice, "synthesize", None)
            if not callable(synthesize) or not hasattr(pipersynth, "SynthesisConfig"):
                return False
            parameters = inspect.signature(synthesize).parameters
            return "text" in parameters and "syn_config" in parameters
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
        loudness_options = options.get("loudness")
        loudness = (
            pipersynth.LoudnessConfig(**loudness_options)
            if isinstance(loudness_options, Mapping)
            else pipersynth.LoudnessConfig()
        )
        config = pipersynth.SynthesisConfig(
            length_scale=options.get("length_scale"),
            noise_scale=options.get("noise_scale"),
            noise_w_scale=options.get("noise_w_scale"),
            normalize_audio=bool(options.get("normalize_audio", True)),
            volume=float(options.get("volume", 1.0)),
            loudness=loudness,
        )

        @contextmanager
        def session() -> Any:
            try:
                yield PiperSynthEngineSession(voice, config)
            finally:
                voice.close()

        return session()


__all__ = ["PiperSynthEngineAdapter", "PiperSynthEngineSession"]
