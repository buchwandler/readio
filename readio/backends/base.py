"""Backend-neutral synthesis contracts used by Readio."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ..config import ReadioConfig
    from ..document import InputDocument
    from ..lexicons import LexiconCatalogEntry
    from ..models import ModelInfo
    from ..plan import PlanDiagnostic, ReadioPlan, SynthesisCandidate
    from ..synthesis import ResolvedSynthesis


@dataclass(frozen=True, slots=True)
class DiscoveryInfo:
    """Common provenance returned by backend discovery operations."""

    registry_source: str | None = None
    cache_fallback: bool = False
    offline: bool = False
    refreshed: bool = False


@dataclass(frozen=True, slots=True)
class BackendResolution:
    """Concrete backend selection produced during plan resolution."""

    backend: str
    model: str | None = None
    voice: str | None = None
    language: str | None = None
    metadata: Any = None


class PreparedSynthesisSession(Protocol):
    """The part of a backend session consumed by Readio's renderer."""

    def prepare_units(self, text: str, *, unit: str) -> AbstractContextManager[Any]: ...


class SynthesisBackend(Protocol):
    """Adapter boundary for one Readio synthesis runtime."""

    id: str
    ssmd_provider: str
    supported_options: frozenset[str]

    def version(self) -> str | None: ...

    def discover_models(
        self,
        *,
        language: str | None = None,
        status: str | None = None,
        offline: bool = False,
        refresh: bool = False,
        preference: str = "auto",
    ) -> tuple[tuple[ModelInfo, ...], DiscoveryInfo]: ...

    def discover_lexicons(
        self,
        *,
        language: str | None = None,
        model: str | None = None,
        offline: bool = False,
        refresh: bool = False,
        preference: str = "auto",
    ) -> tuple[tuple[LexiconCatalogEntry, ...], DiscoveryInfo]: ...

    def resolve_defaults(
        self, candidate: SynthesisCandidate
    ) -> tuple[SynthesisCandidate, tuple[PlanDiagnostic, ...]]: ...

    def validate_selection(self, selection: BackendResolution) -> tuple[PlanDiagnostic, ...]: ...

    def tokenizer_config_for_synthesis(self, synthesis: object) -> Any: ...
    def short_sentence_config_for_synthesis(self, synthesis: object) -> Any: ...
    def language_detection_config_for_synthesis(
        self, synthesis: object, document: InputDocument | None = None
    ) -> Any: ...
    def build_ssmd_render_config(
        self,
        text: str,
        cfg: ReadioConfig,
        additional_bindings: dict[str, str] | None = None,
        synthesis: ResolvedSynthesis | None = None,
    ) -> Any: ...
    def pipeline_config_for_document(
        self,
        document: InputDocument,
        cfg: ReadioConfig,
        *,
        ssmd_voice_bindings: dict[str, str] | None = None,
        synthesis: ResolvedSynthesis | None = None,
    ) -> Any: ...
    def pipeline_config_from_plan(self, plan: ReadioPlan, document: InputDocument) -> Any: ...
    def open_resolved_session(
        self,
        document: InputDocument,
        cfg: ReadioConfig,
        *,
        ssmd_voice_bindings: dict[str, str] | None = None,
        synthesis: ResolvedSynthesis | None = None,
    ) -> Any: ...
    def open_legacy_session(self, document: InputDocument, settings: object) -> Any: ...
    def create_playback_player(self, sample_rate: int, settings: object, channels: int) -> Any: ...

    def open_session(
        self, plan: ReadioPlan, document: InputDocument
    ) -> AbstractContextManager[PreparedSynthesisSession]: ...


__all__ = [
    "BackendResolution",
    "DiscoveryInfo",
    "PreparedSynthesisSession",
    "SynthesisBackend",
]
