"""Content identities for canonical, speech-only project artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from ..project import canonical_json

SCHEMA = "readio.canonical-speech.v1"
SEGMENT_KEY_SCHEMA = "readio.synthesis-segment.v2"


def _to_dict(value: Any) -> Any:
    method = getattr(value, "to_dict", None)
    if callable(method):
        return method()
    if isinstance(value, Mapping):
        return {str(key): _to_dict(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_to_dict(item) for item in value]
    return value

def _normalized_token_value(token: Any, name: str) -> Any:
    value = getattr(token, name, None)
    if not isinstance(value, str):
        return value
    normalized = value.casefold()
    if name == "pos" and normalized == "propn":
        return "noun"
    if name == "tag" and normalized in {"nn", "nnp", "nnps"}:
        return "nn"
    return normalized


def segment_tokens(plan: Any, segment: Any) -> list[dict[str, Any]]:
    tokens = tuple(getattr(plan, "tokens", ()) or ())
    result: list[dict[str, Any]] = []
    for index in tuple(getattr(segment, "token_indices", ()) or ()):
        if not 0 <= int(index) < len(tokens):
            continue
        token = tokens[int(index)]
        result.append(
            {
                "text": getattr(token, "text", None),
                "language": _normalized_token_value(token, "language"),
                "lemma": _normalized_token_value(token, "lemma"),
                "pos": _normalized_token_value(token, "pos"),
                "tag": _normalized_token_value(token, "tag"),
                "morph": _normalized_token_value(token, "morph"),
            }
        )
    return result


def synthesis_directives(segment: Any) -> dict[str, Any]:
    directives = getattr(segment, "directives", None)
    return {
        "voice": _to_dict(getattr(directives, "voice", None)),
        "pronunciation": _to_dict(getattr(directives, "pronunciation", None)),
    }

def _canonical_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    source = profile.get("canonical", profile)
    if not isinstance(source, Mapping):
        return {"value": _to_dict(source)}
    editorial = {
        "speed",
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
    }
    result = {
        str(key): _to_dict(value)
        for key, value in source.items()
        if key not in editorial
    }
    options = result.get("options")
    if isinstance(options, Mapping):
        result["options"] = {
            str(key): value for key, value in options.items() if key not in editorial
        }
    return result


def segment_speech_payload(
    plan: Any,
    segment: Any,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    """Return only inputs capable of changing canonical speech audio."""
    return {
        "schema": SCHEMA,
        "text": getattr(segment, "text", ""),
        "language": getattr(segment, "language", None),
        "voice": synthesis_directives(segment).get("voice"),
        "pronunciation": synthesis_directives(segment).get("pronunciation"),
        "tokens": segment_tokens(plan, segment),
        "synthesis_directives": synthesis_directives(segment),
        "profile": _canonical_profile(profile),
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
    "SCHEMA",
    "SEGMENT_KEY_SCHEMA",
    "segment_speech_hash",
    "segment_speech_payload",
    "segment_synthesis_key",
    "segment_tokens",
    "synthesis_directives",
]
