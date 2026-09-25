"""Read-only discovery services for engines, targets, models, voices, and lexicons."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from .. import formats as formats_internal
from .. import lexicons as lexicons_internal
from .. import models as models_internal
from .. import voices as voices_internal
from ..engines.discovery import discover_targets
from ..engines.registry import engine_status, get_engine, normalize_engine_id
from ..errors import ReadioError
from ..jsonutil import JsonValue, json_value
from ..lexicons import discover_lexicon_catalog
from ..models import discover_model_info, get_model_info
from ..voices import discover_voice_catalog, resolve_voice_selector
from . import errors as api_errors
from .types import (
    AudioFormatInfo,
    CatalogDiscovery,
    CatalogListing,
    DiscoveryOptions,
    EngineInfo,
    LexiconInfo,
    LexiconQuery,
    ModelInfo,
    ModelQuery,
    ModelVoiceInfo,
    SynthesisTargetInfo,
    TargetQuery,
    VoiceInfo,
    VoiceQuery,
    VoiceResolution,
)

if TYPE_CHECKING:
    from .app import Readio


_ENGINE_PACKAGES = {"pykokoro": "pykokoro", "piper": "pipersynth", "pocket": "pocketsynth"}
_DEFAULT_DISCOVERY = DiscoveryOptions()
_DEFAULT_TARGET_QUERY = TargetQuery()
_DEFAULT_MODEL_QUERY = ModelQuery()
_DEFAULT_VOICE_QUERY = VoiceQuery()
_DEFAULT_LEXICON_QUERY = LexiconQuery()


class CatalogService:
    """Expose current first-party discovery through serializable API values."""

    def __init__(self, app: Readio) -> None:
        self._app = app

    def normalize_engine(self, engine: str) -> str:
        """Return the canonical ID for a known engine alias, otherwise the input."""
        return normalize_engine_id(engine)

    def engines(self) -> tuple[EngineInfo, ...]:
        try:
            rows: list[EngineInfo] = []
            for engine_id, status in engine_status().items():
                adapter = get_engine(engine_id) if status["adapter"] else None
                rows.append(
                    EngineInfo(
                        id=engine_id,
                        version=status["version"],
                        registered=status["adapter"],
                        installed=status["package"],
                        runnable=status["status"] == "ready",
                        capabilities=adapter.capabilities() if adapter is not None else None,
                        missing_dependency=(
                            _ENGINE_PACKAGES.get(engine_id, engine_id)
                            if not status["package"]
                            else None
                        ),
                    )
                )
            return tuple(rows)
        except ReadioError:
            raise
        except Exception as error:
            raise api_errors.translate_exception(
                error,
                error_type=api_errors.DiscoveryError,
                code="catalog.engines_failed",
            ) from error

    def targets(
        self,
        query: TargetQuery = _DEFAULT_TARGET_QUERY,
        *,
        discovery: DiscoveryOptions = _DEFAULT_DISCOVERY,
    ) -> tuple[SynthesisTargetInfo, ...]:
        try:
            result = discover_targets(
                engine=query.engine,
                language=query.language,
                offline=discovery.offline,
                refresh=discovery.refresh,
                preference=discovery.preference,
            )
            targets = tuple(self._target_info(target) for target in result.targets)
            return tuple(
                target
                for target in targets
                if (query.status is None or target.status == query.status)
                and (not query.runnable_only or target.runtime_available)
            )
        except ReadioError:
            raise
        except Exception as error:
            raise self._discovery_error(error, "catalog.targets_failed") from error

    def models(
        self,
        query: ModelQuery = _DEFAULT_MODEL_QUERY,
        *,
        discovery: DiscoveryOptions = _DEFAULT_DISCOVERY,
    ) -> tuple[ModelInfo, ...]:
        return self.models_listing(query, discovery=discovery).items

    def models_listing(
        self,
        query: ModelQuery = _DEFAULT_MODEL_QUERY,
        *,
        discovery: DiscoveryOptions = _DEFAULT_DISCOVERY,
    ) -> CatalogListing[ModelInfo]:
        try:
            engine = self.normalize_engine(query.engine) if query.engine else None
            if engine not in {None, "pykokoro"}:
                targets = self.targets(
                    TargetQuery(engine=engine, language=query.language, status=query.status),
                    discovery=discovery,
                )
                return CatalogListing(
                    tuple(self._target_model(target) for target in targets),
                    self._discovery_metadata(None, discovery),
                )

            discovered, raw_discovery = discover_model_info(
                language=query.language,
                status=query.status,
                offline=discovery.offline,
                refresh=discovery.refresh,
                preference=discovery.preference,
                engine=engine,
            )
            models = tuple(self._model_info(model) for model in discovered)
            if query.engine is not None:
                return CatalogListing(models, self._discovery_metadata(raw_discovery, discovery))

            other_targets = self.targets(
                TargetQuery(language=query.language, status=query.status),
                discovery=discovery,
            )
            extras = tuple(
                self._target_model(target)
                for target in other_targets
                if target.engine != "pykokoro"
            )
            return CatalogListing(
                (*models, *extras), self._discovery_metadata(raw_discovery, discovery)
            )
        except ReadioError:
            raise
        except Exception as error:
            raise self._discovery_error(error, "catalog.models_failed") from error

    def model(
        self,
        model_id: str,
        *,
        discovery: DiscoveryOptions = _DEFAULT_DISCOVERY,
    ) -> ModelInfo:
        return self.model_listing(model_id, discovery=discovery).items[0]

    def model_listing(
        self,
        model_id: str,
        *,
        discovery: DiscoveryOptions = _DEFAULT_DISCOVERY,
    ) -> CatalogListing[ModelInfo]:
        try:
            try:
                info, raw_discovery = get_model_info(
                    model_id,
                    offline=discovery.offline,
                    refresh=discovery.refresh,
                    preference=discovery.preference,
                )
            except models_internal.ModelDiscoveryError as error:
                if error.code != "readio.model_not_found":
                    raise self._discovery_error(error, "catalog.model_not_found") from error
                targets = self.targets(discovery=discovery)
                matches = tuple(target for target in targets if target.id == model_id)
                if len(matches) != 1:
                    raise api_errors.DiscoveryError(
                        f"Model {model_id!r} matched {len(matches)} engine targets.",
                        code="catalog.model_not_found",
                    ) from error
                return CatalogListing(
                    (self._target_model(matches[0]),),
                    self._discovery_metadata(None, discovery),
                )
            return CatalogListing(
                (self._model_info(info),),
                self._discovery_metadata(raw_discovery, discovery),
            )
        except ReadioError:
            raise
        except Exception as error:
            raise self._discovery_error(error, "catalog.model_not_found") from error

    def voices(
        self,
        query: VoiceQuery = _DEFAULT_VOICE_QUERY,
        *,
        discovery: DiscoveryOptions = _DEFAULT_DISCOVERY,
    ) -> tuple[VoiceInfo, ...]:
        return self.voices_listing(query, discovery=discovery).items

    def voices_listing(
        self,
        query: VoiceQuery = _DEFAULT_VOICE_QUERY,
        *,
        discovery: DiscoveryOptions = _DEFAULT_DISCOVERY,
    ) -> CatalogListing[VoiceInfo]:
        engine = self.normalize_engine(query.engine) if query.engine else None
        entries: list[VoiceInfo] = []
        raw_discovery = None
        try:
            if engine in {None, "pykokoro"}:
                found, raw_discovery = discover_voice_catalog(
                    offline=discovery.offline,
                    refresh=discovery.refresh,
                    preference=discovery.preference,
                    engine="pykokoro",
                    language=query.language,
                )
                entries.extend(self._voice_info(entry) for entry in found)
            if engine in {None, "piper"}:
                try:
                    found, piper_discovery = discover_voice_catalog(
                        offline=discovery.offline,
                        refresh=discovery.refresh,
                        preference=discovery.preference,
                        engine="piper",
                        language=query.language,
                    )
                except (ImportError, ValueError):
                    if engine is not None:
                        raise
                else:
                    entries.extend(self._voice_info(entry) for entry in found)
                    raw_discovery = raw_discovery or piper_discovery
            if engine not in {"pykokoro", "piper"}:
                targets = self.targets(
                    TargetQuery(engine=engine, language=query.language),
                    discovery=discovery,
                )
                for target in targets:
                    if target.engine not in {"pykokoro", "piper"}:
                        entries.extend(self._target_voices(target))
        except ReadioError:
            raise
        except Exception as error:
            raise self._discovery_error(error, "catalog.voices_failed") from error

        normalized_query = query.language.casefold().replace("_", "-") if query.language else None
        filtered = tuple(
            item
            for item in entries
            if (query.gender is None or item.gender == query.gender)
            and (query.model is None or item.model == query.model)
            and (engine is None or item.engine == engine)
            and (normalized_query is None or self._language_matches(normalized_query, item))
        )
        return CatalogListing(filtered, self._discovery_metadata(raw_discovery, discovery))

    def voice_listing(
        self,
        selector: str,
        *,
        query: VoiceQuery = _DEFAULT_VOICE_QUERY,
        discovery: DiscoveryOptions = _DEFAULT_DISCOVERY,
    ) -> CatalogListing[VoiceInfo]:
        listing = self.voices_listing(query, discovery=discovery)
        raw = selector.casefold()
        normalized = raw.replace("_", "-")
        matches = tuple(
            item
            for item in listing.items
            if raw in {item.id.casefold(), item.qualified_id.casefold()}
            or (
                item.selector is not None
                and normalized
                in {item.selector.casefold(), item.selector.casefold().replace("_", "-")}
            )
        )
        if not matches:
            raise api_errors.DiscoveryError(
                f"Unknown voice {selector!r}.",
                details={"selector": selector},
                code="catalog.voice_not_found",
            )
        if len(matches) > 1:
            raise api_errors.DiscoveryError(
                f"Voice {selector!r} is ambiguous.",
                details={
                    "selectors": [item.selector for item in matches],
                    "qualified_ids": [item.qualified_id for item in matches],
                },
                code="catalog.voice_ambiguous",
            )
        return CatalogListing(matches, listing.discovery)

    def voice(
        self,
        selector: str,
        *,
        engine: str | None = None,
        discovery: DiscoveryOptions = _DEFAULT_DISCOVERY,
    ) -> VoiceInfo:
        return self.voice_listing(
            selector,
            query=VoiceQuery(engine=engine),
            discovery=discovery,
        ).items[0]

    def resolve_voice(
        self,
        selector: str,
        *,
        engine: str | None = None,
        discovery: DiscoveryOptions = _DEFAULT_DISCOVERY,
    ) -> VoiceResolution:
        try:
            resolved = resolve_voice_selector(
                selector,
                language=None,
                model=None,
                source=None,
                offline=discovery.offline,
                refresh=discovery.refresh,
                preference=discovery.preference,
                engine=engine,
            )
            if resolved is not None and resolved.selector is not None:
                entry = self._voice_info(resolved.catalog_entry) if resolved.catalog_entry else None
                if entry is None:
                    candidates = self.voices(
                        VoiceQuery(model=resolved.model, engine=resolved.engine),
                        discovery=discovery,
                    )
                    entry = next((item for item in candidates if item.id == resolved.voice), None)
                return VoiceResolution(
                    requested=resolved.requested,
                    selector=resolved.selector,
                    language=resolved.language,
                    model=resolved.model,
                    source=resolved.source,
                    voice=resolved.voice,
                    engine=resolved.engine,
                    catalog_entry=entry,
                )

            candidates = self.voices(VoiceQuery(engine=engine), discovery=discovery)
            matches = tuple(
                item
                for item in candidates
                if item.id == selector or item.selector == selector or item.qualified_id == selector
            )
            if len(matches) != 1:
                raise ValueError(
                    f"voice selector {selector!r} matched {len(matches)} catalog entries"
                )
            entry = matches[0]
            return VoiceResolution(
                requested=selector,
                selector=entry.selector,
                language=entry.locale,
                model=entry.model,
                source=entry.source,
                voice=entry.id,
                engine=entry.engine,
                catalog_entry=entry,
            )
        except ReadioError:
            raise
        except Exception as error:
            raise self._discovery_error(error, "catalog.voice_not_found") from error

    def lexicons(
        self,
        query: LexiconQuery = _DEFAULT_LEXICON_QUERY,
        *,
        discovery: DiscoveryOptions = _DEFAULT_DISCOVERY,
    ) -> tuple[LexiconInfo, ...]:
        return self.lexicons_listing(query, discovery=discovery).items

    def lexicons_listing(
        self,
        query: LexiconQuery = _DEFAULT_LEXICON_QUERY,
        *,
        discovery: DiscoveryOptions = _DEFAULT_DISCOVERY,
    ) -> CatalogListing[LexiconInfo]:
        try:
            entries, raw_discovery = discover_lexicon_catalog(
                language=query.language,
                model=query.model,
                engine=query.engine,
                offline=discovery.offline,
                refresh=discovery.refresh,
                preference=discovery.preference,
            )
            return CatalogListing(
                tuple(self._lexicon_info(entry) for entry in entries),
                self._discovery_metadata(raw_discovery, discovery),
            )
        except ReadioError:
            raise
        except Exception as error:
            raise self._discovery_error(error, "catalog.lexicons_failed") from error

    def lexicon_listing(
        self,
        selector: str,
        *,
        query: LexiconQuery = _DEFAULT_LEXICON_QUERY,
        discovery: DiscoveryOptions = _DEFAULT_DISCOVERY,
    ) -> CatalogListing[LexiconInfo]:
        listing = self.lexicons_listing(query, discovery=discovery)
        matches = tuple(
            entry
            for entry in listing.items
            if entry.selector == selector or entry.asset_id == selector
        )
        if not matches:
            raise api_errors.DiscoveryError(
                f"Lexicon {selector!r} was not found.",
                details={"selector": selector},
                code="catalog.lexicon_not_found",
            )
        if len(matches) > 1:
            raise api_errors.DiscoveryError(
                f"Lexicon {selector!r} is ambiguous.",
                details={
                    "selectors": [entry.selector for entry in matches],
                    "asset_ids": [entry.asset_id for entry in matches],
                },
                code="catalog.lexicon_ambiguous",
            )
        return CatalogListing(matches, listing.discovery)

    def lexicon(
        self,
        selector: str,
        *,
        query: LexiconQuery = _DEFAULT_LEXICON_QUERY,
        discovery: DiscoveryOptions = _DEFAULT_DISCOVERY,
    ) -> LexiconInfo:
        return self.lexicon_listing(selector, query=query, discovery=discovery).items[0]

    def audio_formats(self) -> tuple[AudioFormatInfo, ...]:
        diagnostics = formats_internal.audio_format_diagnostics()
        result = []
        for name, spec in formats_internal.AUDIO_FORMATS.items():
            available = bool(diagnostics[name]["available"])
            reason = None
            if not available:
                reason = (
                    "FFmpeg executable not found"
                    if spec.backend == "ffmpeg"
                    else "libsndfile does not support this format"
                )
            result.append(
                AudioFormatInfo(id=name, suffix=spec.suffix, available=available, reason=reason)
            )
        return tuple(result)

    def _model_info(self, model: models_internal.ModelInfo) -> ModelInfo:
        return ModelInfo(
            id=model.id,
            source=model.source,
            languages=model.languages,
            voices=model.voices,
            default_voice=model.default_voice,
            qualities=model.qualities,
            g2p_backend=model.g2p_backend,
            lexicons=model.lexicons,
            frontend=model.frontend,
            status=model.status,
            experimental=model.experimental,
            runtime_available=model.runtime_available,
            redistribution_allowed=model.redistribution_allowed,
            distribution_id=model.distribution_id,
            provider=model.provider,
            distribution_provider=model.distribution_provider,
            backend=model.backend,
            sample_rate=model.sample_rate,
            max_tokens=model.max_tokens,
            voice_details=tuple(
                ModelVoiceInfo(
                    id=detail.id,
                    gender=detail.gender,
                    language=detail.language,
                    locale=detail.locale,
                    language_label=detail.language_label,
                )
                for detail in model.voice_details
            ),
        )

    def _target_model(self, target: SynthesisTargetInfo) -> ModelInfo:
        default_voice = str(
            target.metadata.get("default_voice") or (target.voices[0] if target.voices else "")
        )
        return ModelInfo(
            id=target.id,
            source=str(target.metadata.get("source") or target.engine),
            languages=target.languages,
            voices=target.voices,
            default_voice=default_voice,
            qualities=target.qualities,
            g2p_backend=(
                str(target.metadata["g2p_backend"]) if target.metadata.get("g2p_backend") else None
            ),
            lexicons=tuple(str(item) for item in target.metadata["lexicons"])
            if isinstance(target.metadata.get("lexicons"), (tuple, list))
            else None,
            frontend=str(target.metadata.get("frontend") or ""),
            status=target.status,
            experimental=target.status == "experimental",
            runtime_available=target.runtime_available,
            redistribution_allowed=bool(target.metadata.get("redistribution_allowed", False)),
            distribution_id=str(target.metadata.get("distribution_id") or target.id),
            provider=str(target.metadata["provider"]) if target.metadata.get("provider") else None,
            backend=target.engine,
            sample_rate=target.sample_rate,
            voice_details=tuple(
                ModelVoiceInfo(
                    id=str(item.get("id", "")),
                    gender=str(item.get("gender", "unknown")),
                    language=str(
                        item.get("language", target.languages[0] if target.languages else "unknown")
                    ),
                    locale=str(
                        item.get("locale", target.languages[0] if target.languages else "unknown")
                    ),
                    language_label=str(
                        item.get(
                            "language_label", target.languages[0] if target.languages else "unknown"
                        )
                    ),
                )
                for item in target.metadata.get("voice_details", ())
                if isinstance(item, dict)
            ),
        )

    def _target_info(self, target) -> SynthesisTargetInfo:
        return SynthesisTargetInfo(
            engine=target.engine,
            id=target.id,
            display_name=target.display_name,
            languages=tuple(target.languages),
            status=target.status,
            runtime_available=target.runtime_available,
            sample_rate=target.sample_rate,
            voices=tuple(target.voices),
            speakers=tuple(target.speakers),
            qualities=tuple(target.qualities),
            aliases=tuple(target.aliases),
            capabilities=frozenset(target.capabilities),
            metadata=cast(dict[str, JsonValue], json_value(target.metadata)),
        )

    def _voice_info(self, entry: voices_internal.VoiceCatalogEntry) -> VoiceInfo:
        return VoiceInfo(
            selector=entry.selector,
            id=entry.id,
            gender=entry.gender,
            language=entry.language,
            locale=entry.locale,
            language_label=entry.language_label,
            model=entry.model,
            source=entry.source,
            default=entry.default,
            status=entry.status,
            experimental=entry.experimental,
            runtime_available=entry.runtime_available,
            distribution_id=entry.distribution_id,
            provider=entry.provider,
            engine=entry.engine,
            slot=entry.slot,
            selector_language=entry.selector_language,
            selector_engine_code=entry.selector_engine_code,
        )

    def _target_voices(self, target: SynthesisTargetInfo) -> tuple[VoiceInfo, ...]:
        details = target.metadata.get("voice_details", ())
        indexed = (
            {
                str(item.get("id")): item
                for item in details
                if isinstance(item, dict) and item.get("id") is not None
            }
            if isinstance(details, (list, tuple))
            else {}
        )
        language = target.languages[0] if target.languages else "unknown"
        return tuple(
            VoiceInfo(
                selector=None,
                id=voice,
                gender=str(indexed.get(voice, {}).get("gender", "unknown")),
                language=str(indexed.get(voice, {}).get("language", language)),
                locale=str(indexed.get(voice, {}).get("locale", language)),
                language_label=str(indexed.get(voice, {}).get("language_label", language)),
                model=target.id,
                source=target.engine,
                default=voice == target.metadata.get("default_voice"),
                status=target.status,
                experimental=target.status == "experimental",
                runtime_available=target.runtime_available,
                distribution_id=target.id,
                provider=str(target.metadata["provider"])
                if target.metadata.get("provider")
                else None,
                engine=target.engine,
            )
            for voice in target.voices
        )

    def _lexicon_info(self, entry: lexicons_internal.LexiconCatalogEntry) -> LexiconInfo:
        return LexiconInfo(
            selector=entry.selector,
            engine=entry.engine,
            language=entry.language,
            locale=entry.locale,
            asset_id=entry.asset_id,
            data_backend=entry.data_backend,
            default=entry.default,
            installed=entry.installed,
            models=entry.models,
            model_support=entry.model_support,
            display_name=entry.display_name,
            phoneme_encoding=entry.phoneme_encoding,
            data_version=entry.data_version,
        )

    def _language_matches(self, requested: str, voice: VoiceInfo) -> bool:
        locale = voice.locale.casefold().replace("_", "-")
        language = voice.language.casefold().replace("_", "-")
        if "-" in requested:
            return requested in {locale, language}
        base = requested.partition("-")[0]
        return locale.partition("-")[0] == base or language.partition("-")[0] == base

    def _discovery_metadata(
        self, raw: object | None, options: DiscoveryOptions
    ) -> CatalogDiscovery:
        return CatalogDiscovery(
            registry_source=str(getattr(raw, "registry_source", "engine-adapters")),
            cache_fallback=bool(getattr(raw, "cache_fallback", False)),
            offline=bool(getattr(raw, "offline", options.offline)),
            refreshed=bool(getattr(raw, "refreshed", False)),
        )

    def _discovery_error(self, error: Exception, code: str) -> api_errors.DiscoveryError:
        if isinstance(error, models_internal.ModelDiscoveryError):
            return api_errors.DiscoveryError(
                str(error),
                source_path=getattr(error, "source_path", None),
                details={"cause_code": error.code},
                code=error.code,
            )
        return cast(
            api_errors.DiscoveryError,
            api_errors.translate_exception(
                error,
                error_type=api_errors.DiscoveryError,
                code=code,
            ),
        )


__all__ = ["CatalogService"]
