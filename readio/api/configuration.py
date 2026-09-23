"""Persistent configuration and language-profile operations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from .. import config as config_internal
from ..config import LanguageSettings, ReadioConfig
from ..errors import ReadioError
from ..models import ModelDiscoveryError, get_model_info, validate_language_settings
from ..voices import resolve_voice_selector
from . import errors as api_errors
from .types import DiscoveryOptions

if TYPE_CHECKING:
    from .app import Readio


_DEFAULT_DISCOVERY = DiscoveryOptions()


class ConfigurationService:
    """Read and persist configuration without mutating a Readio instance."""

    def __init__(self, app: Readio) -> None:
        self._app = app

    def path(self) -> Path:
        return config_internal.config_path()

    def load(self, path: Path | None = None) -> ReadioConfig:
        try:
            return config_internal.load_config(path.expanduser() if path is not None else None)
        except ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.InputError,
                code="config.invalid",
                source_path=path,
            ) from error

    def defaults(self) -> ReadioConfig:
        return config_internal.default_config()

    def validate(self, config: ReadioConfig) -> ReadioConfig:
        try:
            return config_internal.validate_config(config)
        except ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.InvalidRequestError,
                code="config.invalid",
            ) from error

    def save(
        self,
        config: ReadioConfig,
        *,
        path: Path | None = None,
        overwrite: bool = False,
    ) -> Path:
        target = (path or self.path()).expanduser()
        if target.exists() and not overwrite:
            raise api_errors.OutputError(
                f"configuration already exists: {target}",
                source_path=target,
                code="config.exists",
            )
        self.validate(config)
        try:
            return config_internal.save_config(config, target)
        except ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.OutputError,
                code="config.write_failed",
                source_path=target,
            ) from error

    def set_value(
        self,
        key: str,
        value: object,
        *,
        path: Path | None = None,
    ) -> ReadioConfig:
        try:
            current = self.load(path)
            updated = config_internal.set_config_value(current, key, value)
            if not isinstance(updated, ReadioConfig):
                raise TypeError("configuration update did not return ReadioConfig")
            self.validate(updated)
            config_internal.save_config(updated, path.expanduser() if path is not None else None)
            return updated
        except ReadioError:
            raise
        except Exception as error:
            error_type = api_errors.OutputError if isinstance(error, OSError) else api_errors.InvalidRequestError
            raise api_errors.translate_exception(
                error,
                error_type=error_type,
                code="config.set_failed",
                source_path=path,
            ) from error

    def language_profiles(self) -> Mapping[str, LanguageSettings]:
        return dict(sorted(self._app.config.languages.items()))

    def language_profile(self, language: str) -> LanguageSettings | None:
        try:
            _matched, settings = config_internal.language_profile(self._app.config, language)
            return settings
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.InvalidRequestError,
                code="config.language_invalid",
            ) from error

    def set_language_profile(
        self,
        language: str,
        settings: LanguageSettings,
        *,
        validate_runtime: bool = True,
        discovery: DiscoveryOptions = _DEFAULT_DISCOVERY,
    ) -> LanguageSettings:
        try:
            base = self._config_for_persistence()
            normalized = config_internal.normalize_language_key(language)
            resolved_language = normalized
            resolved_settings = settings
            if validate_runtime:
                resolved_language, resolved_settings = self._resolve_profile(
                    normalized,
                    settings,
                    discovery=discovery,
                )
            updated = replace(
                base,
                schema=2,
                languages={**base.languages, resolved_language: resolved_settings},
            )
            config_internal.validate_config(updated)
            config_internal.save_config(updated)
            return resolved_settings
        except ModelDiscoveryError as error:
            raise api_errors.ResolutionError(
                str(error),
                details={"cause_code": error.code},
                code=error.code,
            ) from error
        except ReadioError:
            raise
        except Exception as error:
            error_type = api_errors.OutputError if isinstance(error, OSError) else api_errors.InvalidRequestError
            raise api_errors.translate_exception(
                error,
                error_type=error_type,
                code="config.language_profile_failed",
            ) from error

    def reset_language_profile(self, language: str) -> None:
        try:
            base = self._config_for_persistence()
            normalized = config_internal.normalize_language_key(language)
            profiles = dict(base.languages)
            if normalized not in profiles:
                raise api_errors.InvalidRequestError(
                    f"No persisted language profile for {normalized!r}.",
                    code="config.language_profile_not_found",
                )
            del profiles[normalized]
            updated = replace(base, schema=2, languages=profiles)
            config_internal.save_config(updated)
        except ReadioError:
            raise
        except Exception as error:
            error_type = api_errors.OutputError if isinstance(error, OSError) else api_errors.InvalidRequestError
            raise api_errors.translate_exception(
                error,
                error_type=error_type,
                code="config.language_profile_reset_failed",
            ) from error

    def _config_for_persistence(self) -> ReadioConfig:
        path = self.path()
        return self.load(path) if path.exists() else self._app.config

    def _resolve_profile(
        self,
        language: str,
        settings: LanguageSettings,
        *,
        discovery: DiscoveryOptions,
    ) -> tuple[str, LanguageSettings]:
        profile_language = language
        model_id = settings.model
        source = settings.source
        voice = settings.voice
        quality = settings.quality
        if voice is not None:
            resolution = resolve_voice_selector(
                voice,
                language=language,
                model=model_id,
                source=source,
                offline=discovery.offline,
                refresh=discovery.refresh,
                preference=source or discovery.preference,
                engine=settings.engine,
            )
            if resolution is not None and resolution.selector is not None:
                profile_language = resolution.language or language
                model_id = resolution.model
                source = resolution.source
                voice = resolution.voice
        resolved = replace(
            settings,
            model=model_id,
            source=source,
            voice=voice,
            quality=quality,
        )
        if model_id is None:
            return profile_language, resolved
        model, _result = get_model_info(
            model_id,
            offline=discovery.offline,
            refresh=discovery.refresh,
            preference=source or discovery.preference,
            backend=settings.engine,
        )
        source = source or model.source
        voice = voice or model.default_voice
        if quality is None and model.qualities:
            quality = "fp32" if "fp32" in model.qualities else model.qualities[0]
        resolved = replace(resolved, source=source, voice=voice, quality=quality)
        validated = validate_language_settings(profile_language, resolved, model)
        return profile_language, validated

__all__ = ["ConfigurationService"]
