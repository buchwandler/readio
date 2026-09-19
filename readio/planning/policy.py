"""Planning policy for Readio semantic planning.

This module provides the PlanningPolicy class that resolves
document and configuration settings into a PlannerConfig.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class PlanningPolicy:
    """Policy for semantic planning that combines engine requirements with Readio config.

    This class resolves language, unit, SSMD parsing, pause policy,
    linguistics, language aliases, and semantic directives into
    a configuration suitable for UtterancePlanner.
    """

    language: str = "en-us"
    unit: Literal["paragraph", "sentence"] = "paragraph"
    text_preparation: Literal["identity", "spokenform"] = "identity"
    document_format: Literal["plain", "ssmd"] = "plain"
    ssmd_provider: str | None = None
    ssmd_voice_bindings: dict[str, str] = field(default_factory=dict)
    pause_mode: str = "auto"
    spacy_policy: str | None = None
    language_aliases: dict[str, str] = field(default_factory=dict)
    language_detection: str | None = None
    detect_languages: tuple[str, ...] = ()

    pauses: Any | None = None
    linguistics: Any | None = None
    ssmd: Any | None = None
    overlap_mode: str = "snap"
    diagnostics: bool = True

    def to_planner_config(self, engine_config: Any = None) -> dict[str, Any]:
        """Convert to a planner configuration dict.

        Args:
            engine_config: Engine-specific planner configuration to merge.

        Returns:
            Combined planner configuration.
        """
        config: dict[str, Any] = {
            "language": self.language,
            "unit": self.unit,
            "text_preparation": self.text_preparation,
            "document_format": self.document_format,
            "pause_mode": self.pause_mode,
            "overlap_mode": self.overlap_mode,
            "diagnostics": self.diagnostics,
        }
        for key in ("pauses", "linguistics", "ssmd"):
            value = getattr(self, key)
            if value is not None:
                config[key] = value

        if self.ssmd_provider is not None:
            config["ssmd_provider"] = self.ssmd_provider
        if self.ssmd_voice_bindings:
            config["ssmd_voice_bindings"] = self.ssmd_voice_bindings
        if self.spacy_policy is not None:
            config["spacy"] = self.spacy_policy
        if self.language_aliases:
            config["language_aliases"] = self.language_aliases
        if self.language_detection is not None:
            config["language_detection"] = self.language_detection
            config["detect_languages"] = self.detect_languages

        if engine_config is not None:
            if isinstance(engine_config, dict):
                values = dict(engine_config)
            else:
                values = {
                    key: getattr(engine_config, key)
                    for key in getattr(engine_config, "__dataclass_fields__", {})
                    if getattr(engine_config, key, None) is not None
                }
            from dataclasses import fields

            from utterplan import PlannerConfig

            semantic_keys = {item.name for item in fields(PlannerConfig)}
            config.update({key: value for key, value in values.items() if key in semantic_keys})

        return config

    @classmethod
    def from_readio_config(cls, cfg: Any) -> PlanningPolicy:
        """Create a PlanningPolicy from a ReadioConfig.

        Args:
            cfg: Readio configuration.

        Returns:
            PlanningPolicy instance.
        """
        from ..synthesis import resolve_synthesis

        resolved = resolve_synthesis(cfg)

        return cls(
            language=resolved.language,
            unit=getattr(cfg, "unit", "paragraph"),
            text_preparation=getattr(cfg, "text_preparation", "identity"),
            document_format=getattr(cfg, "document_format", "plain"),
            ssmd_provider=getattr(cfg.ssmd, "voice_provider", None)
            if hasattr(cfg, "ssmd")
            else None,
            pause_mode=resolved.pause_mode,
            spacy_policy=resolved.spacy if hasattr(resolved, "spacy") else None,
            language_aliases=dict(getattr(cfg, "language_aliases", {})),
            language_detection=resolved.language_detection
            if hasattr(resolved, "language_detection")
            else None,
            detect_languages=tuple(getattr(resolved, "detect_languages", ()) or ()),
        )


__all__ = ["PlanningPolicy"]
