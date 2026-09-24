from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from . import api
from .progress import TerminalProgress

_KNOWN_DOCUMENT_SUFFIXES = frozenset({".txt", ".ssmd", ".md", ".markdown", ".mdown", ".mkd"})


def normalize_positional_input(args: argparse.Namespace) -> None:
    positional = tuple(getattr(args, "text", ()) or ())
    explicit_file = getattr(args, "file", None)

    if explicit_file is not None and positional:
        raise ValueError("provide either positional text/path or --file, not both")
    if explicit_file is not None or not positional or len(positional) != 1:
        return
    if getattr(args, "input_format", "auto") == "text":
        return

    raw = positional[0]
    candidate = Path(raw).expanduser()
    try:
        exists = candidate.exists()
    except OSError as error:
        raise ValueError(f"cannot inspect positional input path {raw!r}: {error}") from error

    if exists:
        if not candidate.is_file():
            raise ValueError(f"positional input path is not a regular file: {candidate}")
        args.file = candidate
        args.text = []
        return

    if (
        candidate.suffix.lower() in _KNOWN_DOCUMENT_SUFFIXES
        or "/" in raw
        or "\\" in raw
        or raw.startswith((".", "~"))
    ):
        raise ValueError(
            f"positional input {raw!r} looks like a file path, but it does not exist; "
            "correct the path or use --input-format text to speak it literally"
        )


def read_document(args: argparse.Namespace) -> api.Document:
    if getattr(args, "file", None) is not None and getattr(args, "text", None):
        raise ValueError("provide either positional text/path or --file, not both")
    if args.file is not None:
        return api.document_from_file(args.file, input_format=args.input_format)
    input_format = args.input_format if args.input_format != "auto" else "text"
    if args.text:
        return api.document_from_text(" ".join(args.text), input_format=input_format)
    if sys.stdin.isatty():
        raise ValueError("provide text, --file PATH, or pipe text on stdin")
    return api.document_from_text(sys.stdin.read(), input_format=input_format)


def parse_voice_bindings(values: Sequence[str]) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for value in values:
        if value.count("=") != 1:
            raise ValueError("--voice-bind must use ROLE=VOICE_ID")
        role, voice_id = value.split("=", 1)
        if not role or not voice_id:
            raise ValueError("--voice-bind must use non-empty ROLE=VOICE_ID")
        if role in bindings:
            raise ValueError(f"duplicate --voice-bind role {role!r}")
        bindings[role] = voice_id
    return bindings


def synthesis_request_from_args(
    args: argparse.Namespace,
    *,
    default_language: str | None = None,
) -> api.SynthesisRequest:
    return api.SynthesisRequest(
        language=getattr(args, "lang", None) or default_language,
        engine=getattr(args, "engine", None),
        model=getattr(args, "model", None),
        model_source=getattr(args, "model_source", None),
        quality=getattr(args, "quality", None),
        voice=getattr(args, "voice", None),
        lexicons=tuple(args.lexicons) if getattr(args, "lexicons", None) is not None else None,
        speaker=getattr(args, "speaker", None),
        clear_lexicons=bool(getattr(args, "no_lexicons", False)),
        auto_lexicons=bool(getattr(args, "auto_lexicons", False)),
        g2p_fallback=getattr(args, "g2p_fallback", None),
        spacy=getattr(args, "spacy", None),
        short_sentence=getattr(args, "short_sentence", None),
        lexicon_data_policy=getattr(args, "lexicon_data_policy", None),
        language_detection=getattr(args, "language_detection", None),
        detect_languages=(
            tuple(args.detect_languages)
            if getattr(args, "detect_languages", None) is not None
            else None
        ),
        allow_experimental=bool(getattr(args, "allow_experimental", False)),
        speed=getattr(args, "speed", None),
        pause_mode=getattr(args, "pause_mode", None),
        unit=getattr(args, "unit", None),
        offline=bool(getattr(args, "offline", False)),
        refresh=bool(getattr(args, "refresh", False)),
    )


def prompt_missing_voices(
    analysis: api.SSMDAnalysis,
    config: api.ReadioConfig,
    synthesis: api.SynthesisRequest | None = None,
    bindings: Mapping[str, str] | None = None,
) -> dict[str, str]:
    unresolved = set(analysis.unresolved_references) - set(bindings or ())
    references = tuple(item for item in analysis.voice_references if item.reference in unresolved)
    resolved_model = getattr(synthesis, "resolved_model", None)
    available = (
        tuple(resolved_model.voices)
        if resolved_model is not None
        else tuple(config.voices[analysis.provider].ids)
    )
    print(
        f"SSMD uses {len(references)} unconfigured voice references "
        f"for provider {analysis.provider!r}:"
    )
    print()
    for item in references:
        print(f"  {item.reference} ({item.count} uses)")
    print()
    print("Available voices:")
    for index, voice in enumerate(available, start=1):
        print(f"  {index}. {voice}")

    bindings: dict[str, str] = {}
    for item in references:
        while True:
            choice = input(f"Voice for {item.reference} [enter number or voice ID]: ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(available):
                bindings[item.reference] = available[int(choice) - 1]
                break
            if choice in available:
                bindings[item.reference] = choice
                break
            print(f"unknown voice {choice!r}; choose a number or configured voice ID")

    print()
    print("Using for this render:")
    for role, voice in bindings.items():
        print(f"  {role} -> {voice}")
    return bindings


def build_plan_request(
    args: argparse.Namespace,
    *,
    document: api.Document,
    synthesis: api.SynthesisRequest,
    output: api.OutputRequest,
    voice_bindings: Mapping[str, str],
    operation: Literal["render", "speak"],
    requested_format: str | None = None,
) -> api.PlanRequest:
    source_kind: Literal["file", "literal", "stdin"] = (
        "file"
        if getattr(args, "file", None) is not None
        else "literal"
        if getattr(args, "text", None)
        else "stdin"
    )
    return api.PlanRequest(
        operation=operation,
        input=api.InputRequest(
            document=document,
            requested_format=(
                requested_format
                if requested_format is not None
                else getattr(args, "input_format", "auto")
            ),
            selector=getattr(args, "select", "all"),
            source_kind=source_kind,
        ),
        synthesis=synthesis,
        output=output,
        voice_bindings=voice_bindings,
    )


def public_event_handler(progress: TerminalProgress) -> api.EventHandler:
    return progress.public_event


__all__ = [
    "build_plan_request",
    "normalize_positional_input",
    "parse_voice_bindings",
    "prompt_missing_voices",
    "public_event_handler",
    "read_document",
    "synthesis_request_from_args",
]
