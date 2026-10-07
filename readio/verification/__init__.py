"""Backend-neutral verification algorithms and public value objects."""

from .cases import VerificationCase, get_case, list_cases
from .text import (
    EditDistance,
    TextVerificationResult,
    VerificationThresholds,
    character_error_rate,
    classify_verification,
    edit_distance,
    normalize_text,
    verify_text,
    word_error_rate,
)
from .types import TranscriptionResult, TranscriptSegment, TranscriptWord
from .voice_matrix import VOICE_MATRIX_SCHEMA, filter_runnable_voices, summarize_voice_results

__all__ = [
    "VOICE_MATRIX_SCHEMA",
    "EditDistance",
    "TextVerificationResult",
    "TranscriptSegment",
    "TranscriptWord",
    "TranscriptionResult",
    "VerificationCase",
    "VerificationThresholds",
    "character_error_rate",
    "classify_verification",
    "edit_distance",
    "filter_runnable_voices",
    "get_case",
    "list_cases",
    "normalize_text",
    "summarize_voice_results",
    "verify_text",
    "word_error_rate",
]
