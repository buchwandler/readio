"""CLI management commands that dispatch only through Readio's public facade."""

from __future__ import annotations

import argparse
import dataclasses
import json
from collections.abc import Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Any, cast


class CLIUsageError(ValueError):
    """A syntactically valid command with invalid option combinations or values."""


def add_control_commands(commands: Any) -> None:
    engines = commands.add_parser("engines", help="Discover and inspect VoiceRender engines")
    engine_commands = engines.add_subparsers(dest="engine_command", required=True)
    engine_list = engine_commands.add_parser("list", help="List engines")
    engine_list.add_argument("--json", action="store_true")
    engine_show = engine_commands.add_parser("show", help="Show engine status")
    engine_show.add_argument("engine")
    engine_show.add_argument("--json", action="store_true")
    engine_options = engine_commands.add_parser("options", help="List engine options")
    engine_options.add_argument("engine")
    engine_options.add_argument("--target")
    engine_options.add_argument("--json", action="store_true")

    models = commands.add_parser("models", help="Manage VoiceRender engine models")
    model_commands = models.add_subparsers(dest="model_command", required=True)
    model_list = model_commands.add_parser("list", help="List engine models")
    model_list.add_argument("--engine", required=True)
    model_list.add_argument("--lang")
    filters = model_list.add_mutually_exclusive_group()
    filters.add_argument("--installed", action="store_true")
    filters.add_argument("--available", action="store_true")
    model_list.add_argument("--offline", action="store_true")
    model_list.add_argument("--refresh", action="store_true")
    model_list.add_argument("--json", action="store_true")
    model_show = model_commands.add_parser("show", help="Inspect a model")
    _model_target_arguments(model_show)
    model_install = model_commands.add_parser("install", help="Install a model")
    _model_target_arguments(model_install)
    model_install.add_argument("--source")
    model_install.add_argument("--cache-dir", type=Path)
    model_install.add_argument("--force", action="store_true")
    model_remove = model_commands.add_parser("remove", help="Remove an installed model")
    _model_target_arguments(model_remove)
    model_remove.add_argument("--cache-dir", type=Path)
    model_remove.add_argument("--yes", action="store_true")
    model_verify = model_commands.add_parser("verify", help="Verify an installed model")
    _model_target_arguments(model_verify)
    model_verify.add_argument("--cache-dir", type=Path)

    voices = commands.add_parser("voices", help="Discover voices")
    voice_commands = voices.add_subparsers(dest="voices_command", required=True)
    voice_list = voice_commands.add_parser("list", help="List voices")
    voice_list.add_argument("--engine")
    voice_list.add_argument("--target")
    voice_list.add_argument("--lang")
    voice_list.add_argument("--kind")
    voice_list.add_argument("--offline", action="store_true")
    voice_list.add_argument("--refresh", action="store_true")
    voice_list.add_argument("--json", action="store_true")
    voice_show = voice_commands.add_parser("show", help="Inspect a voice selector")
    voice_show.add_argument("selector")
    voice_show.add_argument("--lang")
    voice_show.add_argument("--json", action="store_true")

    voice = commands.add_parser("voice", help="Create and manage voice artifacts")
    voice_commands = voice.add_subparsers(dest="voice_command", required=True)
    source = voice_commands.add_parser("source", help="Manage reusable voice sources")
    source_commands = source.add_subparsers(dest="source_command", required=True)
    source_create = source_commands.add_parser("create", help="Create a pinned voice source")
    source_kind = source_create.add_mutually_exclusive_group(required=True)
    source_kind.add_argument("--reference-wav", type=Path)
    source_kind.add_argument("--voicefolio-ref")
    source_create.add_argument("--transcript")
    source_create.add_argument("--language")
    source_create.add_argument("--name")
    source_create.add_argument("--metadata", action="append", default=[], metavar="KEY=VALUE")
    source_create.add_argument("-o", "--output", type=Path, required=True)
    source_create.add_argument("--force", action="store_true")
    source_create.add_argument("--json", action="store_true")
    source_show = source_commands.add_parser("show", help="Show a voice source")
    source_show.add_argument("path", type=Path)
    source_show.add_argument("--json", action="store_true")
    source_verify = source_commands.add_parser("verify", help="Validate a voice source")
    source_verify.add_argument("path", type=Path)
    source_verify.add_argument("--offline", action="store_true")
    source_verify.add_argument("--json", action="store_true")

    clone = voice_commands.add_parser("clone", help="Create a reusable cloned voice profile")
    _voice_binding_arguments(clone, require_source=True)
    clone.add_argument("--engine", required=True)
    clone.add_argument("--target", required=True)
    clone.add_argument("--language", required=True)
    clone.add_argument("--role", default="narrator")
    clone.add_argument("--transcript")
    clone.add_argument("--speaker")
    clone.add_argument("--option", action="append", default=[], metavar="KEY=VALUE")
    clone.add_argument("--offline", action="store_true")
    clone.add_argument("--refresh", action="store_true")
    clone.add_argument("-o", "--output", type=Path, required=True)
    clone.add_argument("--force", action="store_true")
    clone.add_argument("--json", action="store_true")

    profile = commands.add_parser("profile", help="Create, inspect, and manage RenderProfiles")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    profile_create = profile_commands.add_parser("create", help="Create a RenderProfile")
    _profile_binding_arguments(profile_create)
    profile_create.add_argument("--engine", required=True)
    profile_create.add_argument("--target", required=True)
    profile_create.add_argument("--language", required=True)
    profile_create.add_argument("--role", default="narrator")
    profile_create.add_argument("--speaker")
    profile_create.add_argument("--option", action="append", default=[], metavar="KEY=VALUE")
    profile_create.add_argument("--offline", action="store_true")
    profile_create.add_argument("--refresh", action="store_true")
    profile_create.add_argument("-o", "--output", type=Path, required=True)
    profile_create.add_argument("--force", action="store_true")
    profile_create.add_argument("--json", action="store_true")
    for name, help_text in (
        ("show", "Show a RenderProfile"),
        ("validate", "Validate a RenderProfile"),
    ):
        command = profile_commands.add_parser(name, help=help_text)
        command.add_argument("profile", type=Path)
        command.add_argument("--json", action="store_true")
    profile_refresh = profile_commands.add_parser(
        "refresh", help="Refresh profile binding metadata"
    )
    profile_refresh.add_argument("profile", type=Path)
    profile_refresh.add_argument("-o", "--output", type=Path)
    profile_refresh.add_argument("--force", action="store_true")
    profile_refresh.add_argument("--json", action="store_true")

    role = profile_commands.add_parser("role", help="Manage logical speaker roles")
    role_commands = role.add_subparsers(dest="role_command", required=True)
    role_list = role_commands.add_parser("list", help="List profile roles")
    role_list.add_argument("profile", type=Path)
    role_list.add_argument("--json", action="store_true")
    role_set = role_commands.add_parser("set", help="Create or replace a logical role")
    role_set.add_argument("profile", type=Path)
    role_set.add_argument("role")
    _profile_binding_arguments(role_set)
    role_set.add_argument("--engine", required=True)
    role_set.add_argument("--target", required=True)
    role_set.add_argument("--language", required=True)
    role_set.add_argument("--speaker")
    role_set.add_argument("--option", action="append", default=[], metavar="KEY=VALUE")
    role_set.add_argument("--offline", action="store_true")
    role_set.add_argument("--refresh", action="store_true")
    role_set.add_argument("--make-default", action="store_true")
    _write_mode_arguments(role_set)
    role_set.add_argument("--json", action="store_true")
    role_remove = role_commands.add_parser("remove", help="Remove a logical role")
    role_remove.add_argument("profile", type=Path)
    role_remove.add_argument("role")
    role_remove.add_argument("--new-default-role")
    _write_mode_arguments(role_remove)
    role_remove.add_argument("--json", action="store_true")
    default_role = profile_commands.add_parser("default-role", help="Set the default logical role")
    default_role.add_argument("profile", type=Path)
    default_role.add_argument("role")
    _write_mode_arguments(default_role)
    default_role.add_argument("--json", action="store_true")


def _model_target_arguments(parser: Any) -> None:
    parser.add_argument("target")
    parser.add_argument("--engine", required=True)
    parser.add_argument("--quality")
    parser.add_argument("--json", action="store_true")


def _voice_binding_arguments(parser: Any, *, require_source: bool = False) -> None:
    group = parser.add_mutually_exclusive_group(required=require_source)
    group.add_argument("--reference-wav", type=Path)
    group.add_argument("--voice-source", type=Path)
    group.add_argument("--voicefolio-ref")


def _profile_binding_arguments(parser: Any) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--voice")
    group.add_argument("--voicefolio-ref")
    group.add_argument("--reference-wav", type=Path)
    group.add_argument("--prepared-voice", type=Path)
    group.add_argument("--voice-source", type=Path)


def _write_mode_arguments(parser: Any) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-o", "--output", type=Path)
    group.add_argument("--in-place", action="store_true")


def _assignments(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        key, separator, content = value.partition("=")
        if not separator or not key:
            raise CLIUsageError(f"option must use KEY=VALUE: {value!r}")
        result[key] = content
    return result


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _json_value(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _emit(result: Any, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"ok": True, "result": _json_value(result)}, ensure_ascii=False))
    else:
        print(_human_summary(result))
    return 0


def _human_summary(value: Any) -> str:
    if isinstance(value, Mapping):
        return "\n".join(f"{key}: {_human_summary(item)}" for key, item in value.items())
    if isinstance(value, (tuple, list)):
        if not value:
            return "No results."
        return "\n".join(_human_summary(item) for item in value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        if hasattr(value, "roles") and hasattr(value, "default_role"):
            lines = [
                f"Profile ID: {getattr(value, 'profile_id', None) or 'not assigned'}",
                f"Default role: {value.default_role}",
                f"Planning policy: {cast(Any, value).planning}",
                f"Pause policy: {cast(Any, value).pauses}",
            ]
            for role, binding in value.roles.items():
                details = getattr(binding, "binding", binding)
                lines.append(f"Role {role}: {_human_summary(details)}")
            return "\n".join(lines)
        return "\n".join(
            f"{field.name}: {_human_summary(getattr(value, field.name))}"
            for field in dataclasses.fields(value)
        )
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def run_control_command(args: argparse.Namespace, api: Any) -> int:
    command = args.command
    if command == "engines":
        if args.engine_command == "list":
            result = api.list_engines()
        elif args.engine_command == "show":
            result = api.inspect_engine(args.engine)
        else:
            result = api.list_engine_options(args.engine, target=args.target)
        return _emit(result, as_json=args.json)

    if command == "models":
        if args.model_command == "list":
            result = api.list_engine_models(
                args.engine,
                language=args.lang,
                installed=True if args.installed else None,
                available=args.available,
                offline=args.offline,
                refresh=args.refresh,
            )
        elif args.model_command == "show":
            result = api.inspect_engine_model(args.target, engine=args.engine, quality=args.quality)
        elif args.model_command == "install":
            result = api.install_engine_model(
                args.target,
                engine=args.engine,
                quality=args.quality,
                source=args.source,
                cache_dir=args.cache_dir,
                force=args.force,
            )
        elif args.model_command == "remove":
            if not args.yes:
                raise CLIUsageError("model removal requires --yes")
            result = api.remove_engine_model(
                args.target, engine=args.engine, quality=args.quality, cache_dir=args.cache_dir
            )
        else:
            result = api.verify_engine_model(
                args.target, engine=args.engine, quality=args.quality, cache_dir=args.cache_dir
            )
        return _emit(result, as_json=args.json)

    if command == "voices":
        if args.voices_command == "list":
            result = api.list_voices(
                engine=args.engine,
                target=args.target,
                language=args.lang,
                kind=args.kind,
                offline=args.offline,
                refresh=args.refresh,
            )
        else:
            result = api.inspect_voice(args.selector, language=args.lang)
        if args.voices_command == "list" and not args.json:
            print("SELECTOR ENGINE TARGET VOICE/SPEAKER LANGUAGE KIND STATUS")
            for voice in result:
                speaker = getattr(voice, "voice", None) or getattr(voice, "speaker", None) or "-"
                languages = getattr(voice, "languages", ())
                language = ",".join(str(item) for item in languages) if languages else "-"
                kind = getattr(voice, "kind", "-")
                status = (
                    "installed"
                    if getattr(voice, "installed", None) is True
                    else ("available" if getattr(voice, "installed", None) is False else "unknown")
                )
                print(
                    f"{getattr(voice, 'selector', '-')} {getattr(voice, 'engine', '-')} "
                    f"{getattr(voice, 'target_id', '-')} {speaker} {language} "
                    f"{_human_summary(kind)} {status}"
                )
            return 0
        return _emit(result, as_json=args.json)

    if command == "voice":
        if args.voice_command == "source":
            if args.source_command == "create":
                result = api.create_voice_source(
                    reference_wav=args.reference_wav,
                    voicefolio_ref=args.voicefolio_ref,
                    transcript=args.transcript,
                    language=args.language,
                    name=args.name,
                    metadata=_assignments(args.metadata),
                    output=args.output,
                    force=args.force,
                )
            elif args.source_command == "show":
                result = api.show_voice_source(args.path)
            else:
                result = api.verify_voice_source(args.path, offline=args.offline)
            return _emit(result, as_json=args.json)
        if args.voice_command == "clone":
            result = api.clone_voice(
                engine=args.engine,
                target=args.target,
                language=args.language,
                role=args.role,
                reference_wav=args.reference_wav,
                voice_source=args.voice_source,
                voicefolio_ref=args.voicefolio_ref,
                transcript=args.transcript,
                speaker=args.speaker,
                options=_assignments(args.option),
                offline=args.offline,
                refresh=args.refresh,
                output=args.output,
                force=args.force,
            )
        return _emit(result, as_json=args.json)

    if command == "profile":
        if args.profile_command == "create":
            result = api.create_profile(
                engine=args.engine,
                target=args.target,
                language=args.language,
                role=args.role,
                voice=args.voice,
                voicefolio_ref=args.voicefolio_ref,
                reference_wav=args.reference_wav,
                prepared_voice=args.prepared_voice,
                voice_source=args.voice_source,
                speaker=args.speaker,
                options=_assignments(args.option),
                offline=args.offline,
                refresh=args.refresh,
                output=args.output,
                force=args.force,
            )
        elif args.profile_command == "show":
            result = api.load_profile(args.profile)
        elif args.profile_command == "validate":
            result = api.validate_profile(args.profile)
        elif args.profile_command == "refresh":
            result = api.refresh_profile(args.profile, output=args.output, force=args.force)
        elif args.profile_command == "role":
            if args.role_command == "list":
                result = api.list_profile_roles(args.profile)
            elif args.role_command == "set":
                result = api.set_profile_role(
                    args.profile,
                    args.role,
                    engine=args.engine,
                    target=args.target,
                    language=args.language,
                    voice=args.voice,
                    voicefolio_ref=args.voicefolio_ref,
                    reference_wav=args.reference_wav,
                    prepared_voice=args.prepared_voice,
                    voice_source=args.voice_source,
                    speaker=args.speaker,
                    options=_assignments(args.option),
                    offline=args.offline,
                    refresh=args.refresh,
                    make_default=args.make_default,
                    output=args.output,
                    in_place=args.in_place,
                )
            else:
                result = api.remove_profile_role(
                    args.profile,
                    args.role,
                    output=args.output,
                    in_place=args.in_place,
                    new_default_role=args.new_default_role,
                )
        else:
            result = api.set_default_profile_role(
                args.profile, args.role, output=args.output, in_place=args.in_place
            )
        return _emit(result, as_json=args.json)
    raise ValueError(f"unsupported control command: {command}")


__all__ = ["CLIUsageError", "add_control_commands", "run_control_command"]
