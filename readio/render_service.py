"""Build specialist-owned option values from a resolved render intent."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal, cast

import audiocompose  # type: ignore[import-not-found]
import utterplan  # type: ignore[import-not-found]

from readio.intent_resolver import ResolvedRenderIntent


def _planner_config(resolved: ResolvedRenderIntent) -> Any | None:
    planning = resolved.intent.planning
    if resolved.intent.preparation.spacy is not None:
        preparation_spacy = resolved.intent.preparation.spacy
        if preparation_spacy == "off":
            planning = replace(
                planning,
                spacy="off",
                spacy_model=None,
                spacy_model_size=None,
                require_spacy=False,
            )
        elif preparation_spacy == "auto":
            planning = replace(
                planning,
                spacy="auto",
                spacy_model=None,
                spacy_model_size=None,
                require_spacy=False,
            )
        else:
            planning = replace(
                planning,
                spacy="on",
                spacy_model=None,
                spacy_model_size=preparation_spacy,
                require_spacy=True,
            )
    if resolved.language is None:
        custom = (
            planning.text_preparation != "auto"
            or planning.overlap_mode != "snap"
            or planning.renderability_mode != "repair"
            or planning.spacy != "auto"
            or planning.spacy_model is not None
            or planning.spacy_model_size is not None
            or planning.require_spacy
        )
        if custom:
            raise ValueError(
                "input.language is required when non-default planning controls are set"
            )
        return None

    linguistics = utterplan.LinguisticsConfig(
        use_spacy=None if planning.spacy == "auto" else planning.spacy == "on",
        spacy_model=planning.spacy_model,
        spacy_model_size=cast(Literal["sm", "md", "lg", "trf"] | None, planning.spacy_model_size),
        require_spacy=planning.require_spacy,
    )
    return utterplan.PlannerConfig(
        language=resolved.language,
        text_preparation=cast(
            Literal["auto", "ssmd", "spokenform", "identity"], planning.text_preparation
        ),
        overlap_mode=cast(Literal["snap", "strict"], planning.overlap_mode),
        renderability_mode=cast(Literal["strict", "repair"], planning.renderability_mode),
        linguistics=linguistics,
    )


def _compose_options(resolved: ResolvedRenderIntent) -> Any:
    compose = resolved.intent.compose
    return audiocompose.BundleComposeOptions(
        sample_rate=compose.sample_rate,
        clip_policy=cast(Literal["clamp", "warn", "error"] | None, compose.clip_policy),
        target_lufs=compose.target_lufs,
        true_peak_ceiling_dbtp=compose.true_peak_ceiling_dbtp,
        peak_policy=cast(Literal["reduce_gain", "error"] | None, compose.peak_policy),
    )
