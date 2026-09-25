"""Request-centric adapter for the published PocketSynth runtime API."""

from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
from collections.abc import Mapping
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

from ..config import normalize_language_key
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
    RenderedSpeech,
    RequestMeasure,
    SpeechRequest,
    validate_rendered_speech,
)
from .catalog import CatalogRequest, SynthesisTarget

POCKET_OPTION_NAMES = frozenset(
    {
        "precision",
        "voice_source",
        "temperature",
        "lsd_steps",
        "max_frames",
        "frames_after_eos",
        "cache_dir",
        "catalog_path",
        "providers",
        "provider_options",
        "session_options",
        "voice_level",
        "force_download",
    }
)
_GENERATION_OPTION_NAMES = frozenset({"temperature", "lsd_steps", "max_frames", "frames_after_eos"})


def _bundle_fields(
    bundle: Any,
) -> tuple[str, str, tuple[str, ...], tuple[str, ...], int | None, Mapping[str, Any]]:
    metadata = getattr(bundle, "metadata", None)
    metadata = metadata if isinstance(metadata, Mapping) else {}
    bundle_id = str(getattr(bundle, "id", None) or getattr(bundle, "bundle_id", ""))
    display_name = str(metadata.get("display_name") or metadata.get("name") or bundle_id)
    language = metadata.get("language")
    languages = (
        (normalize_language_key(language),) if isinstance(language, str) and language else ()
    )
    raw_voices = metadata.get("predefined_voice_names", getattr(bundle, "voices", ()))
    voices = tuple(str(name) for name in raw_voices if isinstance(name, str))
    profiles = metadata.get("profiles", {})
    qualities = (
        tuple(sorted(str(name) for name in profiles)) if isinstance(profiles, Mapping) else ()
    )
    sample_rate = getattr(bundle, "sample_rate", None)
    return (
        bundle_id,
        display_name,
        languages,
        voices,
        sample_rate,
        {
            **dict(metadata),
            "profiles": dict(profiles) if isinstance(profiles, Mapping) else {},
            "qualities": qualities,
        },
    )


def _matches_language(requested: str, available: str) -> bool:
    requested = normalize_language_key(requested)
    available = normalize_language_key(available)
    return requested == available or requested.split("-", 1)[0] == available.split("-", 1)[0]


def _voice_source(selection: EngineSelection) -> Mapping[str, Any] | None:
    source = selection.metadata.get("voice_source")
    if not isinstance(source, Mapping):
        return None
    kind = source.get("kind")
    value = source.get("value", source.get("path"))
    if kind not in {"named", "reference"} or not isinstance(value, str) or not value:
        raise ValueError("pocket.voice_source_invalid: expected a named or reference voice source")
    if kind == "reference" and not isinstance(source.get("sha256"), str):
        raise ValueError("pocket.voice_source_invalid: reference voice source requires sha256")
    return {"kind": kind, "value": value, "sha256": source.get("sha256")}


def _voice_identity(selection: EngineSelection) -> dict[str, str] | None:
    source = _voice_source(selection)
    if source is not None:
        if source["kind"] == "reference":
            return {"kind": "reference", "sha256": str(source["sha256"])}
        return {"kind": "named", "value": str(source["value"])}
    if selection.voice:
        return {"kind": "named", "value": selection.voice}
    return None


def _reference_path(source: Mapping[str, Any]) -> Path:
    return Path(str(source["value"])).expanduser()


def _reference_sha256(source: Mapping[str, Any]) -> str:
    path = _reference_path(source)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = str(source["sha256"])
    if digest != expected:
        raise ValueError(
            f"pocket.reference_voice_changed: expected {expected}, found {digest} for {path}"
        )
    return digest


def _manager(*, options: Mapping[str, Any], offline: bool) -> Any:
    import pocketsynth

    return pocketsynth.BundleAssetManager(
        cache_dir=options.get("cache_dir"),
        catalog_path=options.get("catalog_path"),
        offline=offline,
    )


def _find_bundle(
    bundles: tuple[Any, ...], target_id: str
) -> (
    tuple[Any, tuple[str, str, tuple[str, ...], tuple[str, ...], int | None, Mapping[str, Any]]]
    | None
):
    for bundle in bundles:
        fields = _bundle_fields(bundle)
        aliases = getattr(bundle, "aliases", ()) or ()
        if target_id == fields[0] or target_id in aliases:
            return bundle, fields
    return None


def _pocket_version() -> str | None:
    try:
        return importlib.metadata.version("pocketsynth")
    except importlib.metadata.PackageNotFoundError:
        return None


def _translate_pocket_error(
    error: Exception,
    selection: EngineSelection,
    request: SpeechRequest | None = None,
    *,
    opening: bool = False,
) -> EngineSynthesisError:
    import pocketsynth

    error_map = (
        ("SynthesisInputTooLongError", SpeechRequestTooLongError),
        ("EmptyTextError", EmptySpeechTextError),
        ("InvalidLanguageError", InvalidEngineLanguageError),
        ("BundleLanguageError", InvalidEngineLanguageError),
        ("InvalidVoiceError", InvalidEngineVoiceError),
        ("InvalidGenerationConfigError", InvalidEngineOptionError),
        ("InvalidRequestError", InvalidSpeechRequestError),
        ("UnsupportedFeatureError", UnsupportedSynthesisFeatureError),
        ("BundleNotFoundError", InvalidEngineModelError),
        ("UnsupportedBundleError", InvalidEngineModelError),
        ("ModelInferenceError", EngineBackendError),
        ("RuntimeClosedError", EngineBackendError),
    )
    error_type: type[EngineSynthesisError] = EngineBackendError
    for native_name, mapped_type in error_map:
        native_type = getattr(pocketsynth, native_name, None)
        if isinstance(native_type, type) and isinstance(error, native_type):
            error_type = mapped_type
            break
    else:
        if opening and isinstance(error, (TypeError, ValueError)):
            error_type = InvalidEngineOptionError
        elif isinstance(error, (TypeError, ValueError)):
            error_type = InvalidSpeechRequestError

    context: dict[str, Any] = {
        "engine": "pocket",
        "engine_version": _pocket_version(),
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
            source="pocketsynth.runtime",
        )
    return error_type(str(error), **context)


def _voice_level_metadata(value: Any, mode: str) -> dict[str, Any]:
    metadata = dict(value) if isinstance(value, Mapping) else {}
    return {
        "mode": metadata.get("mode", mode),
        "applied": bool(metadata.get("applied", False)),
        "gain_db": metadata.get("gain_db"),
        "source": metadata.get("source", "none"),
        "reason": metadata.get("reason"),
        "calibration_identity": metadata.get("identity", metadata.get("calibration_identity")),
        "calibration_revision": metadata.get("catalog_revision", metadata.get("bundle_revision")),
    }


class PocketSynthEngineSession:
    """Adapt one exact Readio request to PocketSynth's strict runtime API."""

    def __init__(
        self, runtime: Any, selection: EngineSelection, generation: Any, voice_level: Any
    ) -> None:
        self._runtime = runtime
        self._selection = selection
        self._generation = generation
        self._voice_level = voice_level
        self._voices: dict[tuple[str, str], Any] = {}

    def _voice(self, request: SpeechRequest) -> Any:
        request_source = request.options.get("voice_source")
        source = None
        if isinstance(request_source, Mapping):
            source_selection = EngineSelection(
                engine=self._selection.engine,
                target_id=self._selection.target_id,
                language=self._selection.language,
                voice=request.voice,
                options=self._selection.options,
                metadata={"voice_source": request_source},
            )
            source = _voice_source(source_selection)
        if source is None:
            source = _voice_source(self._selection)
        if source is None:
            voice_name = request.voice or self._selection.voice
            if not voice_name:
                raise ValueError("PocketSynth requires a predefined or reference voice")
            source = {"kind": "named", "value": voice_name}
        if source["kind"] == "reference":
            _reference_sha256(source)
            key = ("reference", str(source["sha256"]))
            voice_input: str | Path = _reference_path(source)
        else:
            key = ("named", str(source["value"]))
            voice_input = str(source["value"])
        if key not in self._voices:
            self._voices[key] = self._runtime.prepare_voice(voice_input)
        return self._voices[key]

    def measure(self, request: SpeechRequest) -> RequestMeasure:
        token_ids = self._runtime.frontend.encode(request.text)
        maximum = getattr(self._runtime.metadata, "max_token_per_chunk", None)
        maximum = int(maximum) if maximum is not None else None
        amount = len(token_ids)
        return RequestMeasure(
            fits=amount <= maximum if maximum is not None else None,
            amount=amount,
            maximum=maximum,
            unit="model_tokens",
            source="pocketsynth.frontend",
        )

    def synthesize(self, request: SpeechRequest) -> RenderedSpeech:
        import pocketsynth

        if request.tokens:
            raise UnsupportedSynthesisFeatureError(
                "PocketSynth does not support linguistic tokens",
                engine="pocket",
                target_id=self._selection.target_id,
                request_id=request.id,
            )
        if request.pronunciation_overrides:
            raise UnsupportedSynthesisFeatureError(
                "PocketSynth does not support pronunciation overrides",
                engine="pocket",
                target_id=self._selection.target_id,
                request_id=request.id,
            )
        requested = normalize_language_key(request.language)
        bundle_language = getattr(self._runtime.metadata, "language", None)
        if isinstance(bundle_language, str) and not _matches_language(requested, bundle_language):
            raise InvalidEngineLanguageError(
                f"Pocket bundle {self._selection.target_id!r} supports {bundle_language!r}, "
                f"not {requested!r}",
                engine="pocket",
                target_id=self._selection.target_id,
                language=request.language,
                request_id=request.id,
            )

        try:
            native = pocketsynth.SynthesisRequest(
                id=request.id, text=request.text, language=request.language
            )
        except EngineSynthesisError:
            raise
        except Exception as exc:
            raise _translate_pocket_error(exc, self._selection, request) from exc
        try:
            voice = self._voice(request)
        except (OSError, ValueError) as exc:
            raise InvalidEngineVoiceError(
                str(exc),
                engine="pocket",
                target_id=self._selection.target_id,
                language=request.language,
                voice=request.voice,
                request_id=request.id,
                native_error_type=type(exc).__name__,
            ) from exc
        try:
            voice = self._voice(request)
        except (OSError, ValueError) as exc:
            raise InvalidEngineVoiceError(
                str(exc),
                engine="pocket",
                target_id=self._selection.target_id,
                language=request.language,
                voice=request.voice,
                request_id=request.id,
                native_error_type=type(exc).__name__,
            ) from exc
        try:
            result = self._runtime.synthesize(
                native,
                voice=voice,
                config=self._generation,
                voice_level=self._voice_level,
            )
        except EngineSynthesisError:
            raise
        except Exception as exc:
            raise _translate_pocket_error(exc, self._selection, request) from exc

        metadata = dict(result.metadata)
        metadata.update(
            bundle_id=self._selection.target_id,
            precision=self._selection.options.get("precision", "int8"),
            voice=_voice_identity_for_request(self._selection, request),
            voice_level=_voice_level_metadata(
                metadata.get("voice_level_application"), self._voice_level.mode
            ),
        )
        measured = self.measure(request)
        metadata.setdefault("token_count", measured.amount)
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
            engine="pocket",
            engine_version=_pocket_version(),
            target_id=self._selection.target_id,
        )


def _voice_identity_for_request(
    selection: EngineSelection, request: SpeechRequest
) -> dict[str, str] | None:
    source = request.options.get("voice_source")
    if isinstance(source, Mapping):
        request_selection = EngineSelection(
            engine=selection.engine,
            target_id=selection.target_id,
            language=selection.language,
            options=selection.options,
            metadata={"voice_source": source},
        )
        return _voice_identity(request_selection)
    return _voice_identity(selection) or (
        {"kind": "named", "value": request.voice} if request.voice else None
    )


class PocketSynthEngineAdapter:
    """Pocket bundle discovery, selection, runtime and request adaptation."""

    id = "pocket"
    package_name = "pocketsynth"

    def version(self) -> str | None:
        try:
            return importlib.metadata.version(self.package_name)
        except importlib.metadata.PackageNotFoundError:
            return None

    def compatible_api(self) -> bool:
        try:
            import pocketsynth

            runtime = getattr(pocketsynth, "PocketRuntime", None)
            required = (
                "SynthesisRequest",
                "SynthesisResult",
                "GenerationConfig",
                "VoiceLevelConfig",
                "SynthesisInputTooLongError",
                "BundleAssetManager",
            )
            if runtime is None or not all(hasattr(pocketsynth, name) for name in required):
                return False
            if not all(hasattr(runtime, name) for name in ("from_resolved", "prepare_voice")):
                return False
            parameters = inspect.signature(runtime.synthesize).parameters
            return all(name in parameters for name in ("request", "voice", "config", "voice_level"))
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
            voice_binding_namespace="pocket",
            supports_named_voices=True,
            supports_reference_voice=True,
            supports_qualities=True,
            option_names=POCKET_OPTION_NAMES,
            supports_voice_level_calibration=True,
            supports_request_measurement=True,
        )

    def discover(self, request: CatalogRequest) -> tuple[SynthesisTarget, ...]:
        try:
            bundles = _manager(options={}, offline=request.offline).list_bundles(
                language=request.language,
                refresh=request.refresh,
            )
        except ImportError:
            return ()
        targets = []
        for bundle in bundles:
            bundle_id, display_name, languages, voices, sample_rate, metadata = _bundle_fields(
                bundle
            )
            targets.append(
                SynthesisTarget(
                    engine=self.id,
                    id=bundle_id,
                    display_name=display_name,
                    languages=languages,
                    sample_rate=sample_rate,
                    voices=voices,
                    qualities=tuple(metadata["qualities"]),
                    aliases=tuple(getattr(bundle, "aliases", ()) or ()),
                    capabilities=frozenset({"predefined_voice", "reference_voice"}),
                    metadata=metadata,
                )
            )
        return tuple(targets)

    def resolve(self, request: Any) -> tuple[EngineSelection, tuple[Any, ...]]:
        language = normalize_language_key(getattr(request, "language", None) or "en-us")
        options = dict(getattr(request, "options", {}) or {})
        options.update(dict(getattr(request, "engine_options", {}) or {}))
        target_id = getattr(request, "target_id", None) or options.get("bundle")
        if not target_id:
            raise ValueError(
                "pocket.bundle_required: PocketSynth requires a bundle; pass --model <bundle-id> "
                f"or run `readio voices list --engine pocket --lang {language}`."
            )
        precision = options.get("precision", options.get("quality", "int8"))
        if not isinstance(precision, str) or not precision:
            raise ValueError("pocket.precision_invalid: precision must be int8 or fp32")
        voice_source = options.get("voice_source")
        metadata = {"voice_source": dict(voice_source)} if isinstance(voice_source, Mapping) else {}
        runtime_options = {
            key: value
            for key, value in options.items()
            if key in POCKET_OPTION_NAMES and key != "voice_source"
        }
        runtime_options["precision"] = precision
        return (
            EngineSelection(
                engine=self.id,
                target_id=str(target_id),
                language=language,
                voice=getattr(request, "voice", None),
                speaker=getattr(request, "speaker", None),
                options=runtime_options,
                metadata=metadata,
                offline=bool(getattr(request, "offline", False)),
                refresh=bool(getattr(request, "refresh", False)),
            ),
            (),
        )

    def validate_selection(self, selection: EngineSelection) -> tuple[Any, ...]:
        from ..plan import PlanDiagnostic

        try:
            import pocketsynth
            from onnxvoice.errors import OnnxVoiceError
        except ImportError as exc:
            return (
                PlanDiagnostic(
                    code="pocket.runtime_unavailable",
                    severity="error",
                    message=f"PocketSynth runtime dependencies are unavailable: {exc}",
                    field="synthesis.engine",
                ),
            )
        try:
            pocketsynth.GenerationConfig(
                temperature=float(selection.options.get("temperature", 0.7)),
                lsd_steps=int(selection.options.get("lsd_steps", 1)),
                max_frames=selection.options.get("max_frames"),
                frames_after_eos=selection.options.get("frames_after_eos"),
            )
        except (TypeError, ValueError) as exc:
            return (
                PlanDiagnostic(
                    code="pocket.generation_config_invalid",
                    severity="error",
                    message=f"Invalid PocketSynth generation options: {exc}",
                    field="render.options",
                ),
            )
        try:
            bundles = _manager(options=selection.options, offline=selection.offline).list_bundles(
                refresh=selection.refresh
            )
        except (
            OSError,
            ValueError,
            pocketsynth.PocketSynthError,
            OnnxVoiceError,
        ) as exc:
            return (
                PlanDiagnostic(
                    code="pocket.catalog_unavailable",
                    severity="error",
                    message=f"PocketSynth bundle catalog is unavailable: {exc}",
                    field="synthesis.engine",
                ),
            )
        found = _find_bundle(bundles, selection.target_id)
        if found is None:
            return (
                PlanDiagnostic(
                    code="pocket.bundle_not_found",
                    severity="error",
                    message=f"PocketSynth bundle {selection.target_id!r} was not found.",
                    field="render.target.id",
                ),
            )
        _bundle, fields = found
        bundle_id, _display_name, languages, voices, _sample_rate, metadata = fields
        diagnostics = []
        if languages and not any(_matches_language(selection.language, item) for item in languages):
            diagnostics.append(
                PlanDiagnostic(
                    code="engine_language_incompatible",
                    severity="error",
                    message=(
                        f"PocketSynth bundle {bundle_id!r} supports {', '.join(languages)}, "
                        f"not {selection.language!r}."
                    ),
                    field="render.target.language",
                )
            )
        precision = str(selection.options.get("precision", "int8"))
        if precision not in metadata["qualities"]:
            diagnostics.append(
                PlanDiagnostic(
                    code="pocket.precision_unavailable",
                    severity="error",
                    message=(
                        f"Precision {precision!r} is not available on PocketSynth bundle "
                        f"{bundle_id!r}."
                    ),
                    field="render.options.precision",
                )
            )
        try:
            source = _voice_source(selection)
        except ValueError as exc:
            return (
                PlanDiagnostic(
                    code="pocket.voice_source_invalid",
                    severity="error",
                    message=str(exc),
                    field="render.target.voice",
                ),
            )
        voice_kind = (
            source["kind"] if source is not None else ("named" if selection.voice else None)
        )
        voice_name = str(source["value"]) if source is not None else selection.voice
        if voice_kind is None:
            diagnostics.append(
                PlanDiagnostic(
                    code="pocket.voice_required",
                    severity="error",
                    message="Select a PocketSynth predefined voice or reference WAV voice.",
                    field="render.target.voice",
                )
            )
        elif voice_kind == "named" and voice_name not in voices:
            diagnostics.append(
                PlanDiagnostic(
                    code="pocket.voice_not_found",
                    severity="error",
                    message=(
                        f"Predefined voice {voice_name!r} is not available on "
                        f"PocketSynth bundle {bundle_id!r}."
                    ),
                    field="render.target.voice",
                )
            )
        elif voice_kind == "reference" and source is not None:
            try:
                _reference_sha256(source)
            except (OSError, ValueError) as exc:
                diagnostics.append(
                    PlanDiagnostic(
                        code="pocket.reference_voice_invalid",
                        severity="error",
                        message=str(exc),
                        field="render.target.voice",
                    )
                )
        return tuple(diagnostics)

    def target_metadata(self, selection: EngineSelection) -> Mapping[str, Any]:
        try:
            import pocketsynth
            from onnxvoice.errors import OnnxVoiceError
        except ImportError:
            return {}
        try:
            bundles = _manager(options=selection.options, offline=selection.offline).list_bundles(
                refresh=selection.refresh
            )
        except (OSError, ValueError, pocketsynth.PocketSynthError, OnnxVoiceError):
            return {}
        found = _find_bundle(bundles, selection.target_id)
        if found is None:
            return {}
        bundle_id, _display_name, languages, voices, sample_rate, metadata = found[1]
        result: dict[str, Any] = {
            "bundle_id": bundle_id,
            "languages": languages,
            "predefined_voices": voices,
            "sample_rate": sample_rate,
            "max_token_per_chunk": metadata.get("max_token_per_chunk"),
            "precision_profiles": metadata["qualities"],
            "source_revision": metadata.get("source_revision"),
        }
        if "voice_source" in selection.metadata:
            result["voice_source"] = selection.metadata["voice_source"]
        return result

    def canonical_synthesis_identity(self, selection: EngineSelection) -> Mapping[str, Any]:
        voice = _voice_identity(selection)
        if voice is None:
            voice = {"kind": "named", "value": selection.voice or ""}
        return {
            "source_revision": selection.metadata.get("source_revision"),
            "engine": self.id,
            "engine_version": self.version(),
            "target_id": selection.target_id,
            "language": normalize_language_key(selection.language),
            "precision": selection.options.get("precision", "int8"),
            "voice_level": selection.options.get("voice_level", "off"),
            "voice": voice,
            "generation": {
                key: selection.options[key]
                for key in sorted(_GENERATION_OPTION_NAMES)
                if key in selection.options
            },
        }

    def open(self, selection: EngineSelection) -> AbstractContextManager[PocketSynthEngineSession]:
        import pocketsynth

        options = dict(selection.options)
        try:
            generation = pocketsynth.GenerationConfig(
                temperature=float(options.get("temperature", 0.7)),
                lsd_steps=int(options.get("lsd_steps", 1)),
                max_frames=options.get("max_frames"),
                frames_after_eos=options.get("frames_after_eos"),
            )
            voice_level = pocketsynth.VoiceLevelConfig(mode=options.get("voice_level", "off"))
            bundle = _manager(options=options, offline=selection.offline).resolve_bundle(
                selection.target_id,
                precision=str(options.get("precision", "int8")),
                refresh_catalog=selection.refresh,
                force_download=bool(options.get("force_download", False)),
            )
            runtime = pocketsynth.PocketRuntime.from_resolved(
                bundle,
                cache_dir=options.get("cache_dir"),
                offline=selection.offline,
                providers=options.get("providers"),
                provider_options=options.get("provider_options"),
                session_options=options.get("session_options"),
            )
        except Exception as exc:
            raise _translate_pocket_error(exc, selection, opening=True) from exc

        @contextmanager
        def session() -> Any:
            try:
                yield PocketSynthEngineSession(runtime, selection, generation, voice_level)
            finally:
                runtime.close()

        return session()
