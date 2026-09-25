"""Engine-neutral typed UtterPlan policy for Readio."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from utterplan import LinguisticsConfig, PauseConfig, PlannerConfig, SSMDConfig


def _linguistics_from_spacy_policy(policy: str | None) -> LinguisticsConfig:
    """Translate Readio's spaCy policy into UtterPlan settings."""
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
    """Readio's semantic planning settings, independent of synthesis engines."""

    language: str = "en-us"
    unit: Literal["paragraph", "sentence"] = "paragraph"
    text_preparation: Literal["identity", "spokenform"] = "spokenform"
    document_format: Literal["plain", "ssmd"] = "plain"
    pause_mode: str = "auto"
    pause_weak: float = 0.15
    pause_clause: float = 0.3
    pause_sentence: float = 0.6
    pause_paragraph: float = 1.0
    pause_parenthetical: float = 0.15
    pause_voice_change: float = 0.15
    pause_enabled: bool = True
    spacy_policy: str | None = "auto"
    language_aliases: dict[str, str] = field(default_factory=dict)
    language_detection: str | None = None
    detect_languages: tuple[str, ...] = ()
    ssmd: SSMDConfig = field(default_factory=SSMDConfig)
    overlap_mode: Literal["snap", "strict"] = "snap"
    diagnostics: bool = True

    def to_planner_config(self) -> PlannerConfig:
        """Build UtterPlan's typed planner configuration."""
        return PlannerConfig(
            language=self.language,
            document_format=self.document_format,
            text_preparation=self.text_preparation,
            unit=self.unit,
            pauses=PauseConfig(
                mode=self.pause_mode,
                weak=self.pause_weak,
                clause=self.pause_clause,
                sentence=self.pause_sentence,
                paragraph=self.pause_paragraph,
                parenthetical=self.pause_parenthetical,
                voice_change=self.pause_voice_change,
                enabled=self.pause_enabled,
            ),
            linguistics=_linguistics_from_spacy_policy(self.spacy_policy),
            ssmd=self.ssmd,
            overlap_mode=self.overlap_mode,
            language_aliases=dict(self.language_aliases),
            diagnostics=self.diagnostics,
        )

    @classmethod
    def from_semantic_config(
        cls,
        cfg: Any,
        *,
        document_format: str = "plain",
    ) -> PlanningPolicy:
        """Create the engine-free policy from Readio configuration."""
        reader = getattr(cfg, "reader", cfg)
        return cls(
            language=getattr(reader, "lang", "en-us"),
            unit=getattr(reader, "unit", "sentence"),
            text_preparation=getattr(reader, "text_preparation", "spokenform"),
            document_format=document_format,
            pause_mode=getattr(reader, "pause_mode", "auto"),
            pause_weak=getattr(reader, "pause_weak", 0.15),
            pause_clause=getattr(reader, "pause_clause", 0.3),
            pause_sentence=getattr(reader, "pause_sentence", 0.6),
            pause_paragraph=getattr(reader, "pause_paragraph", 1.0),
            pause_parenthetical=getattr(reader, "pause_parenthetical", 0.15),
            pause_voice_change=getattr(reader, "pause_voice_change", 0.15),
            pause_enabled=getattr(reader, "pause_enabled", True),
            spacy_policy=getattr(reader, "spacy", "auto"),
            language_aliases=dict(getattr(cfg, "language_aliases", {}) or {}),
            language_detection=getattr(reader, "language_detection", None),
            detect_languages=tuple(getattr(reader, "detect_languages", None) or ()),
        )

    @classmethod
    def from_readio_config(cls, cfg: Any) -> PlanningPolicy:
        """Create planning policy from the current Readio configuration."""
        return cls.from_semantic_config(cfg)


__all__ = ["PlanningPolicy", "_linguistics_from_spacy_policy"]
