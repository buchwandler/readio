"""Readio's adapter for the public, metadata-only PyKokoro discovery API."""

from __future__ import annotations

import importlib.metadata
import re
from dataclasses import dataclass
from typing import Any

from .config import LanguageSettings, normalize_language_key

PYKOKORO_REQUIRED = ">=0.9.1,<0.10"
_DISCOVERY_PREFERENCES = {"auto", "github", "huggingface", "upstream"}
_RUNTIME_SOURCES = {"github", "huggingface"}


class ModelDiscoveryError(ValueError):
    """A model registry could not be inspected or contained an invalid choice."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "pykokoro.registry_unavailable",
        installed_version: str | None = None,
        distribution_version: str | None = None,
        module_version: str | None = None,
        module_path: str | None = None,
        missing_dependency: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.installed_version = installed_version
        self.distribution_version = distribution_version
        self.module_version = module_version
        self.module_path = module_path
        self.missing_dependency = missing_dependency


@dataclass(frozen=True, slots=True)
class ModelInfo:
    id: str
    source: str
    languages: tuple[str, ...]
    voices: tuple[str, ...]
    default_voice: str
    qualities: tuple[str, ...]
    g2p_backend: str | None
    lexicons: tuple[str, ...] | None
    frontend: str
    status: str
    experimental: bool
    runtime_available: bool
    redistribution_allowed: bool
    distribution_id: str | None = None
    provider: str | None = None
    sample_rate: int | None = None
    max_tokens: int | None = None

    @classmethod
    def from_capabilities(cls, capabilities: Any) -> ModelInfo:
        try:
            return cls(
                id=capabilities.model_id,
                source=capabilities.source,
                languages=tuple(capabilities.languages),
                voices=tuple(capabilities.voices),
                default_voice=capabilities.default_voice,
                qualities=tuple(capabilities.qualities),
                g2p_backend=capabilities.g2p_backend,
                lexicons=None if capabilities.lexicons is None else tuple(capabilities.lexicons),
                frontend=capabilities.frontend,
                status=capabilities.status,
                experimental=capabilities.experimental,
                runtime_available=capabilities.runtime_available,
                redistribution_allowed=capabilities.redistribution_allowed,
                distribution_id=getattr(capabilities, "distribution_id", None),
                provider=getattr(capabilities, "provider", None),
                sample_rate=getattr(capabilities, "sample_rate", None),
                max_tokens=getattr(capabilities, "max_tokens", None),
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise ModelDiscoveryError(
                f"PyKokoro returned invalid model capability metadata: {exc}",
                code="pykokoro.registry_invalid",
            ) from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "languages": list(self.languages),
            "default_voice": self.default_voice,
            "voices": list(self.voices),
            "qualities": list(self.qualities),
            "g2p_backend": self.g2p_backend,
            "lexicons": list(self.lexicons) if self.lexicons is not None else None,
            "lexicons_known": self.lexicons is not None,
            "frontend": self.frontend,
            "experimental": self.experimental,
            "status": self.status,
            "runtime_available": self.runtime_available,
            "redistribution_allowed": self.redistribution_allowed,
            "distribution_id": self.distribution_id,
            "provider": self.provider,
            "sample_rate": self.sample_rate,
            "max_tokens": self.max_tokens,
        }


def validate_discovery_preference(preference: str) -> str:
    if preference not in _DISCOVERY_PREFERENCES:
        choices = ", ".join(sorted(_DISCOVERY_PREFERENCES))
        raise ModelDiscoveryError(
            f"Unknown discovery preference {preference!r}; choose {choices}.",
            code="pykokoro.invalid_options",
        )
    return preference


def _version_supported(version: str) -> bool:
    match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", version)
    if match is None:
        return False
    major, minor = int(match.group(1)), int(match.group(2))
    patch = int(match.group(3) or 0)
    is_09 = (major, minor) == (0, 9)
    is_api_bearing_dev = (major, minor, patch) == (0, 8, 9) and "dev" in version
    return is_09 or is_api_bearing_dev


def _package_metadata() -> str | None:
    try:
        return importlib.metadata.version("pykokoro")
    except importlib.metadata.PackageNotFoundError:
        return None


def _pykokoro_module() -> tuple[Any, str, str | None]:
    try:
        import pykokoro
    except Exception as exc:
        raise ModelDiscoveryError(
            f"Unable to import PyKokoro for model discovery: {exc}",
            code="pykokoro.import_failed",
            distribution_version=_package_metadata(),
        ) from exc
    module_version = str(getattr(pykokoro, "__version__", "unknown"))
    return pykokoro, module_version, _package_metadata()


def _pykokoro_discovery() -> Any:
    pykokoro, version, distribution_version = _pykokoro_module()
    module_path = getattr(pykokoro, "__file__", None)
    if not _version_supported(version):
        raise ModelDiscoveryError(
            "Installed PyKokoro does not support Readio's model-discovery contract "
            f"(module version: {version}; distribution version: {distribution_version or 'unknown'}; "
            f"required: {PYKOKORO_REQUIRED}).",
            code="pykokoro.version_unsupported",
            installed_version=version,
            distribution_version=distribution_version,
            module_version=version,
            module_path=str(module_path) if module_path else None,
        )
    try:
        discovery = pykokoro.discover_models
    except AttributeError as exc:
        raise ModelDiscoveryError(
            "Installed PyKokoro does not provide the model-discovery API required by "
            f"Readio 0.2.0 (module version: {version}; distribution version: "
            f"{distribution_version or 'unknown'}; module: {module_path or 'unknown'}; "
            f"required: {PYKOKORO_REQUIRED} with discover_models).",
            code="pykokoro.discovery_api_missing",
            installed_version=version,
            distribution_version=distribution_version,
            module_version=version,
            module_path=str(module_path) if module_path else None,
        ) from exc
    except (ImportError, ModuleNotFoundError) as exc:
        missing = getattr(exc, "name", None)
        detail = f" (missing dependency: {missing})" if missing else ""
        raise ModelDiscoveryError(
            f"PyKokoro's public discovery API could not be imported{detail}: {exc}",
            code="pykokoro.discovery_api_import_failed",
            installed_version=version,
            distribution_version=distribution_version,
            module_version=version,
            module_path=str(module_path) if module_path else None,
            missing_dependency=missing,
        ) from exc
    except Exception as exc:
        raise ModelDiscoveryError(
            f"PyKokoro's public discovery API could not be imported: {exc}",
            code="pykokoro.discovery_api_import_failed",
            installed_version=version,
            distribution_version=distribution_version,
            module_version=version,
            module_path=str(module_path) if module_path else None,
        ) from exc
    if not callable(discovery):
        raise ModelDiscoveryError(
            f"Installed PyKokoro exposes a non-callable discover_models value "
            f"(module version: {version}; module: {module_path or 'unknown'}).",
            code="pykokoro.discovery_api_missing",
            installed_version=version,
            distribution_version=distribution_version,
            module_version=version,
            module_path=str(module_path) if module_path else None,
        )
    return discovery


def pykokoro_diagnostics() -> dict[str, Any]:
    """Return import/API diagnostics without contacting the model registry."""
    result: dict[str, Any] = {
        "required": PYKOKORO_REQUIRED,
        "distribution_version": _package_metadata(),
        "module_version": None,
        "module_path": None,
        "symbols": {},
    }
    try:
        pykokoro, module_version, distribution_version = _pykokoro_module()
    except ModelDiscoveryError as exc:
        result.update(
            error_code=exc.code,
            error=str(exc),
            distribution_version=exc.distribution_version or result["distribution_version"],
            missing_dependency=exc.missing_dependency,
        )
        return result
    result.update(
        distribution_version=distribution_version,
        module_version=module_version,
        module_path=str(getattr(pykokoro, "__file__", "")) or None,
    )
    if not _version_supported(module_version):
        result["error_code"] = "pykokoro.version_unsupported"
    for name in ("discover_models", "ModelCapabilities", "ModelDiscoveryResult"):
        try:
            value = getattr(pykokoro, name)
        except AttributeError:
            result["symbols"][name] = "missing"
        except (ImportError, ModuleNotFoundError) as exc:
            result["symbols"][name] = {
                "status": "import_failed",
                "error": str(exc),
                "missing_dependency": getattr(exc, "name", None),
            }
        except (
            Exception  # noqa: BLE001
        ) as exc:  # pragma: no cover - defensive lazy import boundary
            result["symbols"][name] = {"status": "import_failed", "error": str(exc)}
        else:
            result["symbols"][name] = (
                "ok" if callable(value) or name != "discover_models" else "missing"
            )
    if result["symbols"].get("discover_models") != "ok" and "error_code" not in result:
        symbol = result["symbols"]["discover_models"]
        result["error_code"] = (
            "pykokoro.discovery_api_import_failed"
            if isinstance(symbol, dict)
            else "pykokoro.discovery_api_missing"
        )
    return result


def _registry_error(exc: Exception) -> ModelDiscoveryError:
    message = str(exc) or exc.__class__.__name__
    name = exc.__class__.__name__.lower()
    code = (
        "pykokoro.registry_invalid"
        if "invalid" in name or "schema" in message.lower()
        else "pykokoro.registry_unavailable"
    )
    return ModelDiscoveryError(message, code=code)


def discover_model_info(
    *,
    language: str | None = None,
    status: str | None = None,
    offline: bool = False,
    refresh: bool = False,
    preference: str = "auto",
) -> tuple[tuple[ModelInfo, ...], Any]:
    if offline and refresh:
        raise ModelDiscoveryError(
            "--offline and --refresh cannot be combined", code="pykokoro.invalid_options"
        )
    preference = validate_discovery_preference(preference)
    try:
        result = _pykokoro_discovery()(offline=offline, refresh=refresh, preference=preference)
    except ModelDiscoveryError:
        raise
    except Exception as exc:
        raise _registry_error(exc) from exc

    try:
        raw_models = result.models
        models = tuple(ModelInfo.from_capabilities(item) for item in raw_models)
    except ModelDiscoveryError:
        raise
    except (AttributeError, TypeError, ValueError) as exc:
        raise ModelDiscoveryError(
            f"PyKokoro returned invalid model discovery data: {exc}",
            code="pykokoro.registry_invalid",
        ) from exc

    requested = normalize_language_key(language) if language else None
    if requested is not None:
        models = tuple(item for item in models if language_matches(requested, item.languages))
    if status is not None:
        models = tuple(item for item in models if item.status == status)
    return models, result


def language_matches(requested: str, declared: tuple[str, ...]) -> bool:
    requested = normalize_language_key(requested)
    requested_base = requested.partition("-")[0]
    return any(
        requested == normalize_language_key(item)
        or requested_base == normalize_language_key(item).partition("-")[0]
        for item in declared
    )


def get_model_info(
    model_id: str,
    *,
    offline: bool = False,
    refresh: bool = False,
    preference: str = "auto",
) -> tuple[ModelInfo, Any]:
    models, result = discover_model_info(offline=offline, refresh=refresh, preference=preference)
    for model in models:
        if model.id == model_id:
            return model, result
    raise ModelDiscoveryError(
        f"Unknown model '{model_id}'. Run `readio models list` to inspect available models.",
        code="pykokoro.model_not_found",
    )


def validate_language_settings(
    language: str,
    settings: LanguageSettings,
    model: ModelInfo,
) -> LanguageSettings:
    """Validate a language profile against already-discovered model metadata."""
    normalized = normalize_language_key(language)
    if not model.runtime_available:
        raise ModelDiscoveryError(
            f"Model '{model.id}' is not available in the installed runtime.",
            code="pykokoro.model_unsupported",
        )
    if model.experimental and not settings.allow_experimental:
        raise ModelDiscoveryError(
            f"Model '{model.id}' uses an experimental PyKokoro frontend. "
            "Re-run with --allow-experimental or persist allow_experimental=true "
            "for this language profile.",
            code="pykokoro.experimental_required",
        )
    if model.status != "ready" and not (model.experimental and settings.allow_experimental):
        raise ModelDiscoveryError(
            f"Model '{model.id}' is not runnable: {model.status}",
            code="pykokoro.model_unsupported",
        )
    if not language_matches(normalized, model.languages):
        declared = ", ".join(model.languages) or "none"
        raise ModelDiscoveryError(
            f"Model '{model.id}' does not declare language '{normalized}'. Declared languages: {declared}",
            code="pykokoro.model_unsupported",
        )
    if settings.source is not None and settings.source not in _RUNTIME_SOURCES:
        raise ModelDiscoveryError(
            f"Model source '{settings.source}' is not supported; use github or huggingface.",
            code="pykokoro.model_source_invalid",
        )
    if settings.source is not None and model.source != settings.source:
        raise ModelDiscoveryError(
            f"Model '{model.id}' resolved from {model.source!r}, not requested source "
            f"{settings.source!r}.",
            code="pykokoro.model_source_invalid",
        )
    if settings.quality is not None and settings.quality not in model.qualities:
        available = ", ".join(model.qualities) or "none"
        raise ModelDiscoveryError(
            f"Quality '{settings.quality}' is not available for model '{model.id}'. Available qualities: {available}",
            code="pykokoro.quality_invalid",
        )
    if settings.voice is not None and settings.voice not in model.voices:
        available = ", ".join(model.voices) or "none"
        raise ModelDiscoveryError(
            f"Voice '{settings.voice}' is not available for model '{model.id}'. Available voices: {available}",
            code="pykokoro.voice_invalid",
        )
    if settings.lexicons is not None and model.lexicons is not None:
        missing = tuple(item for item in settings.lexicons if item not in model.lexicons)
        if missing:
            available = ", ".join(model.lexicons) or "none"
            raise ModelDiscoveryError(
                f"Lexicon '{missing[0]}' is not available for model '{model.id}' / language '{normalized}'. "
                f"Available lexicons: {available}",
                code="pykokoro.lexicon_invalid",
            )
    return settings


__all__ = [
    "PYKOKORO_REQUIRED",
    "ModelDiscoveryError",
    "ModelInfo",
    "discover_model_info",
    "get_model_info",
    "language_matches",
    "pykokoro_diagnostics",
    "validate_discovery_preference",
    "validate_language_settings",
]
