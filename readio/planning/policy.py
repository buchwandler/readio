"""Engine-neutral Utterplan v3 planning policy for Readio."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from utterplan import LinguisticsConfig, PauseConfig, PlannerConfig, SSMDConfig


def _linguistics_from_spacy_policy(policy: str | None) -> LinguisticsConfig:
    """Translate Readio's spaCy policy into Utterplan v3 settings."""
    selected = policy or "auto"
    if selected == "off":
        return LinguisticsConfig(use_spacy=False, require_spacy=False)
    if selected == "auto":
        return LinguisticsConfig(use_spacy=True, require_spacy=False)
    if selected in {"sm", "md", "lg", "trf"}:
        return LinguisticsConfig(
            use_spacy=True,
            spacy_model_size=selected,
            require_spacy=True,
        )
    raise ValueError(
        f"unsupported Readio spaCy policy {selected!r}; expected one of: auto, off, sm, md, lg, trf"
    )


@dataclass(frozen=True, slots=True)
class PlanningPolicy:
    """Readio's semantic policy, independent of a synthesis engine."""

    language: str = "en-us"
    unit: Literal["paragraph", "sentence"] = "paragraph"
    text_preparation: Literal["identity", "spokenform"] = "identity"
    document_format: Literal["plain", "ssmd"] = "plain"
    ssmd_provider: str | None = None
    ssmd_voice_bindings: dict[str, str] = field(default_factory=dict)
    pause_mode: str = "auto"
    spacy_policy: str | None = "auto"
    language_aliases: dict[str, str] = field(default_factory=dict)
    language_detection: str | None = None
    detect_languages: tuple[str, ...] = ()
    ssmd: SSMDConfig = field(default_factory=SSMDConfig)
    overlap_mode: Literal["snap", "strict"] = "snap"
    diagnostics: bool = True

    def to_planner_config(self, engine_config: Any = None) -> PlannerConfig:
        """Build the complete Utterplan v3 planner configuration."""
        if engine_config is not None:
            raise ValueError(
                "engine-specific planner configuration is not accepted by Readio semantic planning"
            )
        return PlannerConfig(
            language=self.language,
            document_format=self.document_format,
            text_preparation=self.text_preparation,
            unit=self.unit,
            pauses=PauseConfig(mode=self.pause_mode),
            linguistics=_linguistics_from_spacy_policy(self.spacy_policy),
            ssmd=self.ssmd,
            overlap_mode=self.overlap_mode,
            language_aliases=dict(self.language_aliases),
            diagnostics=self.diagnostics,
        )

    @classmethod
    def from_semantic_config(cls, cfg: Any, *, document_format: str = "plain") -> PlanningPolicy:
        """Create the engine-free policy from Readio configuration."""
        reader = getattr(cfg, "reader", cfg)
        ssmd = getattr(cfg, "ssmd", None)
        return cls(
            language=getattr(reader, "lang", "en-us"),
            unit=getattr(reader, "unit", "sentence"),
            text_preparation=getattr(reader, "text_preparation", "identity"),
            document_format=document_format,
            ssmd_provider=getattr(ssmd, "voice_provider", None),
            pause_mode=getattr(reader, "pause_mode", "auto"),
            spacy_policy=getattr(reader, "spacy", "auto"),
            language_aliases=dict(getattr(cfg, "language_aliases", {}) or {}),
            language_detection=getattr(reader, "language_detection", None),
            detect_languages=tuple(getattr(reader, "detect_languages", None) or ()),
        )

    @classmethod
    def from_readio_config(cls, cfg: Any) -> PlanningPolicy:
        """Backward-compatible alias for the engine-free semantic policy."""
        return cls.from_semantic_config(cfg)


__all__ = ["PlanningPolicy", "_linguistics_from_spacy_policy"]
