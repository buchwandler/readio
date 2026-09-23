"""The Readio application service container."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

from ..config import ReadioConfig, load_config
from .errors import InvalidRequestError, error_boundary
from .events import EventHandler

if TYPE_CHECKING:
    from .audiobooks import AudiobookService
    from .catalog import CatalogService
    from .configuration import ConfigurationService
    from .diagnostics import DiagnosticsService
    from .ingest import IngestService
    from .projects import ProjectService
    from .roles import RoleService
    from .speech import SpeechService
    from .ssmd import SSMDService
    from .templates import TemplateService


class Readio:
    """Lightweight container for Readio's supported application services."""

    def __init__(
        self,
        config: ReadioConfig | None = None,
        *,
        on_event: EventHandler | None = None,
    ) -> None:
        with error_boundary(error_type=InvalidRequestError, code="config.invalid"):
            self._config = config if config is not None else load_config()
        self._on_event = on_event
        self._services: dict[str, object] = {}

    @property
    def config(self) -> ReadioConfig:
        return self._config

    @property
    def on_event(self) -> EventHandler | None:
        return self._on_event

    def _service(self, module_name: str, class_name: str) -> Any:
        if class_name not in self._services:
            module = import_module(f"{__package__}.{module_name}")
            service_type = getattr(module, class_name)
            self._services[class_name] = service_type(self)
        return self._services[class_name]

    @property
    def speech(self) -> SpeechService:
        return cast("SpeechService", self._service("speech", "SpeechService"))

    @property
    def projects(self) -> ProjectService:
        return cast("ProjectService", self._service("projects", "ProjectService"))

    @property
    def audiobooks(self) -> AudiobookService:
        return cast("AudiobookService", self._service("audiobooks", "AudiobookService"))

    @property
    def catalog(self) -> CatalogService:
        return cast("CatalogService", self._service("catalog", "CatalogService"))

    @property
    def roles(self) -> RoleService:
        return cast("RoleService", self._service("roles", "RoleService"))

    @property
    def ssmd(self) -> SSMDService:
        return cast("SSMDService", self._service("ssmd", "SSMDService"))

    @property
    def configuration(self) -> ConfigurationService:
        return cast(
            "ConfigurationService",
            self._service("configuration", "ConfigurationService"),
        )

    @property
    def templates(self) -> TemplateService:
        return cast("TemplateService", self._service("templates", "TemplateService"))

    @property
    def ingest(self) -> IngestService:
        return cast("IngestService", self._service("ingest", "IngestService"))

    @property
    def diagnostics(self) -> DiagnosticsService:
        return cast("DiagnosticsService", self._service("diagnostics", "DiagnosticsService"))


__all__ = ["Readio"]
