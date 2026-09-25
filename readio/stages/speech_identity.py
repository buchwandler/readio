"""Content identities for canonical, speech-only project artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from ..project import canonical_json
from ..rendering.capacity import CAPACITY_SCHEMA, LOWERING_SCHEMA

SCHEMA = "readio.canonical-speech.v2"
SEGMENT_KEY_SCHEMA = "readio.synthesis-segment.v3"

_COMPOSITION_FIELDS = {
    "rate",
    "volume",
    "pitch",
    "emphasis",
    "pause_mode",
    "sentence_silence",
    "pause_sentence",
    "pause_paragraph",
    "fade_in",
    "fade_out",
    "target_lufs",
    "true_peak_ceiling_dbtp",
}
_OPERATIONAL_FIELDS = {
    "cache_dir",
    "cache_path",
    "catalog_path",
    "download_dir",
    "download_path",
    "model_path",
    "offline",
    "refresh",
    "force_download",
    "providers",
    "provider_options",
    "session_options",
    "progress_callback",
    "verbosity",
    "verbose",
    "runtime_timing",
    "timing_diagnostics",
}


def _to_dict(value: Any) -> Any:
    method = getattr(value, "to_dict", None)
    if callable(method):
        return method()
    if isinstance(value, Mapping):
        return {str(key): _to_dict(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_to_dict(item) for item in value]
    return value


def _clean_profile_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _clean_profile_value(item)
            for key, item in value.items()
            if str(key).casefold() not in _OPERATIONAL_FIELDS
        }
    if isinstance(value, (tuple, list)):
        return [_clean_profile_value(item) for item in value]
    return _to_dict(value)


def canonical_engine_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    """Keep exact output-affecting engine identity and omit operational settings."""
    cleaned = _clean_profile_value(identity)
    if not isinstance(cleaned, Mapping):
        return {"value": cleaned}
    result = {
        str(key): value
        for key, value in cleaned.items()
        if str(key).casefold() not in _COMPOSITION_FIELDS
    }
    options = result.get("options")
    if isinstance(options, Mapping):
        result["options"] = {
            str(key): value
            for key, value in options.items()
            if str(key).casefold() not in _COMPOSITION_FIELDS
        }
    return result


def _canonical_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    source = profile.get("canonical", profile)
    if not isinstance(source, Mapping):
        return {"value": _to_dict(source)}
    return canonical_engine_identity(source)


def segment_tokens(plan: Any, segment: Any) -> list[dict[str, Any]]:
    tokens = tuple(getattr(plan, "tokens", ()) or ())
    segment_start = getattr(segment, "spoken_start", 0)
    result: list[dict[str, Any]] = []
    for index in tuple(getattr(segment, "token_indices", ()) or ()):
        if not 0 <= int(index) < len(tokens):
            continue
        token = tokens[int(index)]
        start = getattr(token, "spoken_start", getattr(token, "start", None))
        end = getattr(token, "spoken_end", getattr(token, "end", None))
        result.append(
            {
                "start": start - segment_start if start is not None else None,
                "end": end - segment_start if end is not None else None,
                "text": _to_dict(getattr(token, "text", None)),
                "language": _to_dict(getattr(token, "language", None)),
                "lemma": _to_dict(getattr(token, "lemma", None)),
                "pos": _to_dict(getattr(token, "pos", None)),
                "tag": _to_dict(getattr(token, "tag", None)),
                "morph": _to_dict(getattr(token, "morph", None)),
            }
        )
    return result


def synthesis_directives(segment: Any) -> dict[str, Any]:
    directives = getattr(segment, "directives", None)
    return {
        "voice": _to_dict(getattr(directives, "voice", None)),
        "pronunciation": _to_dict(getattr(directives, "pronunciation", None)),
    }


def segment_pronunciation_spans(plan: Any, segment: Any) -> list[dict[str, Any]]:
    segment_start = getattr(segment, "spoken_start", 0)
    segment_end = getattr(segment, "spoken_end", segment_start + len(segment.text))
    annotation_ids = set(getattr(segment, "annotation_ids", ()) or ())
    spans: list[dict[str, Any]] = []
    for annotation in tuple(getattr(plan, "annotations", ()) or ()):
        if getattr(annotation, "kind", None) not in {"phoneme", "pronunciation"}:
            continue
        start = getattr(annotation, "spoken_start", None)
        end = getattr(annotation, "spoken_end", None)
        if start is None or end is None:
            continue
        if getattr(annotation, "id", None) not in annotation_ids and not (
            start < segment_end and end > segment_start
        ):
            continue
        attrs = getattr(annotation, "attrs", {}) or {}
        spans.append(
            {
                "start": start - segment_start,
                "end": end - segment_start,
                "phonemes": _to_dict(attrs.get("ph", attrs.get("phonemes"))),
                "language": _to_dict(attrs.get("language")),
                "alphabet": _to_dict(attrs.get("alphabet", "ipa")),
                "attrs": _to_dict(attrs),
            }
        )
    return spans


def segment_speech_payload(
    plan: Any,
    segment: Any,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    """Return only exact inputs capable of changing speech or child boundaries."""
    directives = synthesis_directives(segment)
    canonical_profile = _canonical_profile(profile)
    capabilities = canonical_profile.get("readio_capabilities", {})
    if not isinstance(capabilities, Mapping):
        capabilities = {}
    pronunciation_spans = segment_pronunciation_spans(plan, segment)
    supports_pronunciation_overrides = bool(capabilities.get("pronunciation_overrides", True))
    supports_whole_phonemes = bool(capabilities.get("whole_request_phonemes", True))
    if supports_pronunciation_overrides:
        output_pronunciation_spans = pronunciation_spans
        whole_phonemes: list[Any] = []
    elif supports_whole_phonemes:
        output_pronunciation_spans = [
            span
            for span in pronunciation_spans
            if span["start"] == 0 and span["end"] == len(getattr(segment, "text", ""))
        ]
        whole_phonemes = [span["phonemes"] for span in output_pronunciation_spans]
    else:
        output_pronunciation_spans = []
        whole_phonemes = []
    return {
        "schema": SCHEMA,
        "text": getattr(segment, "text", ""),
        "language": getattr(segment, "language", None),
        "voice": directives["voice"],
        "pronunciation": directives["pronunciation"],
        "pronunciation_spans": output_pronunciation_spans,
        "whole_request_phonemes": whole_phonemes,
        "tokens": (
            segment_tokens(plan, segment)
            if bool(capabilities.get("linguistic_tokens", True))
            else []
        ),
        "synthesis_directives": directives,
        "profile": canonical_profile,
        "lowering": {
            "schema": LOWERING_SCHEMA,
            "capacity_schema": CAPACITY_SCHEMA,
        },
    }


def segment_speech_hash(plan: Any, segment: Any, profile: Mapping[str, Any]) -> str:
    payload = segment_speech_payload(plan, segment, profile)
    return f"sha256:{hashlib.sha256(canonical_json(payload)).hexdigest()}"


def segment_synthesis_key(speech_hash: str, profile_id: str) -> str:
    payload = {
        "schema": SEGMENT_KEY_SCHEMA,
        "speech_hash": speech_hash,
        "profile_id": profile_id,
    }
    return f"sha256:{hashlib.sha256(canonical_json(payload)).hexdigest()}"


__all__ = [
    "CAPACITY_SCHEMA",
    "LOWERING_SCHEMA",
    "SCHEMA",
    "SEGMENT_KEY_SCHEMA",
    "canonical_engine_identity",
    "segment_pronunciation_spans",
    "segment_speech_hash",
    "segment_speech_payload",
    "segment_synthesis_key",
    "segment_tokens",
    "synthesis_directives",
]
