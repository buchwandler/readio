"""CLI management commands that dispatch only through Readio's public facade."""

from __future__ import annotations

import argparse
import dataclasses
import json
import shlex
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

    runtime = commands.add_parser(
        "runtime",
        help="Inspect and select ONNX Runtime execution providers.",
        description="Inspect the imported ONNX Runtime build and select providers for new sessions.",
    )
    runtime_commands = runtime.add_subparsers(dest="runtime_command", required=True)
    runtime_status = runtime_commands.add_parser(
        "status", help="Show the imported ONNX Runtime build and effective provider policy."
    )
    runtime_status.add_argument("--json", action="store_true")
    runtime_list = runtime_commands.add_parser(
        "list", help="List detected ORT distributions and providers reported by the imported build."
    )
    runtime_list.add_argument("--json", action="store_true")
    runtime_use = runtime_commands.add_parser(
        "use", help="Save the default provider policy for new compatible sessions."
    )
    runtime_use.add_argument("providers", metavar="PROVIDERS")
    runtime_use.add_argument("--json", action="store_true")
    runtime_reset = runtime_commands.add_parser("reset", help="Remove the saved provider policy.")
    runtime_reset.add_argument("--json", action="store_true")

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

    profile_document = profile_commands.add_parser(
        "document", help="Inspect SSMD voice roles and create a source-aware profile"
    )
    profile_document_commands = profile_document.add_subparsers(
        dest="profile_document_command", required=True
    )
    profile_document_inspect = profile_document_commands.add_parser(
        "inspect", help="Inspect logical voice roles in an SSMD document"
    )
    profile_document_inspect.add_argument("source", type=Path)
    profile_document_inspect.add_argument("--json", action="store_true")
    profile_document_create = profile_document_commands.add_parser(
        "create", help="Create a complete RenderProfile for an SSMD document"
    )
    profile_document_create.add_argument("source", type=Path)
    profile_document_create.add_argument("--engine", required=True)
    profile_document_create.add_argument("--target", required=True)
    profile_document_create.add_argument("--language", required=True)
    profile_document_create.add_argument(
        "--voice", action="append", default=[], metavar="ROLE=VOICE"
    )
    profile_document_create.add_argument(
        "--same-as", action="append", default=[], metavar="ROLE=ROLE"
    )
    profile_document_create.add_argument(
        "--speaker", action="append", default=[], metavar="ROLE=SPEAKER"
    )
    profile_document_create.add_argument(
        "--option", action="append", default=[], metavar="ROLE:KEY=VALUE"
    )
    profile_document_create.add_argument("--default-role")
    profile_document_create.add_argument("--offline", action="store_true")
    profile_document_create.add_argument("--refresh", action="store_true")
    profile_document_create.add_argument("-o", "--output", type=Path, required=True)
    profile_document_create.add_argument("--force", action="store_true")
    profile_document_create.add_argument("--json", action="store_true")
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


def _role_assignments(values: Sequence[str], option_name: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        role, separator, content = value.partition("=")
        role = role.strip()
        content = content.strip()
        if not separator or not role or not content:
            raise CLIUsageError(f"{option_name} expects ROLE=VALUE: {value!r}")
        if role in result:
            raise CLIUsageError(f"{option_name} was supplied more than once for role {role!r}")
        result[role] = content
    return result


def _role_options(values: Sequence[str]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for value in values:
        role, separator, assignment = value.partition(":")
        key, equals, content = assignment.partition("=")
        role, key, content = role.strip(), key.strip(), content.strip()
        if not separator or not equals or not role or not key or not content:
            raise CLIUsageError(f"--option expects ROLE:KEY=VALUE: {value!r}")
        role_options = result.setdefault(role, {})
        if key in role_options:
            raise CLIUsageError(f"--option was supplied more than once for {role!r}:{key}")
        role_options[key] = content
    return result


def _document_voice_result(requirements: Any) -> dict[str, Any]:
    return {
        "source": requirements.source,
        "roles": [{"role": role.name, "blocks": role.blocks} for role in requirements.roles],
        "default_role_candidate": requirements.default_role_candidate,
        "uses_default_role": requirements.uses_default_role,
        "implicit_blocks": requirements.implicit_blocks,
    }


def _format_document_voice_requirements(requirements: Any) -> str:
    lines = [
        f"Document: {requirements.source}",
        "",
        "VOICE ROLES",
        "ROLE       BLOCKS",
        "---------  ------",
    ]
    if requirements.roles:
        lines.extend(f"{role.name:<10} {role.blocks:>6}" for role in requirements.roles)
    else:
        lines.append("(no explicit voice roles)")
    lines.extend(
        [
            "",
            f"Default role candidate: {requirements.default_role_candidate or 'none'}",
        ]
    )
    if requirements.uses_default_role and not requirements.roles:
        lines.append(f"Unvoiced blocks using the profile default: {requirements.implicit_blocks}")
        lines.append("Create a narrator profile with: readio profile create ...")
        return "\n".join(lines)

    output = requirements.source.with_name(
        f"{requirements.source.name[: -len('.ssmd.md')]}.renderprofile.toml"
    )
    command = [
        "readio",
        "profile",
        "document",
        "create",
        str(requirements.source),
        "--engine",
        "<ENGINE>",
        "--target",
        "<TARGET>",
        "--language",
        "<LANGUAGE>",
    ]
    for role in requirements.roles:
        command.extend(("--voice", f"{role.name}=<VOICE>"))
    command.extend(("-o", str(output)))
    lines.extend(["", "Create a profile:", f"  {shlex.join(command)}"])
    return "\n".join(lines)


def _format_document_profile_created(result: Mapping[str, Any]) -> str:
    lines = [
        f"Created RenderProfile: {result['output']}",
        f"Source: {result['source']}",
        f"Default role: {result['default_role']}",
        "",
        "Bound roles:",
        *(f"  {role}" for role in result["bound_roles"]),
    ]
    aliases = result["aliases"]
    if aliases:
        lines.extend(
            ["", "Role aliases:", *(f"  {role} = {source}" for role, source in aliases.items())]
        )
    profile_id = result.get("profile_id")
    if profile_id:
        lines.extend(["", f"Profile ID: {profile_id}"])
    return "\n".join(lines)


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


def _provider_source_label(source: Any) -> str:
    value = getattr(source, "value", source)
    labels = {
        "default": "built-in default",
        "user_config": "user config",
        "environment": "ONNXVOICE_PROVIDER",
        "environment_variable": "ONNXVOICE_PROVIDER",
    }
    return labels.get(str(value), str(value).replace("_", " "))


def _provider_names(providers: Sequence[str], *, empty: str = "none reported") -> str:
    return ", ".join(str(provider) for provider in providers) or empty


def _runtime_json_result(status: Any) -> dict[str, Any]:
    value = _json_value(status)
    if isinstance(value, Mapping):
        return {"kind": "onnx", **value}
    return {"kind": "onnx", "status": value}


def _format_runtime_status(status: Any) -> str:
    imported = getattr(status, "imported_distribution", None)
    module_version = getattr(status, "module_version", None)
    distribution = str(imported or "not identified")
    if imported and module_version:
        distribution = f"{imported} {module_version}"
    state = str(getattr(status, "distribution_state", "unknown"))
    lines = [
        "ONNX Runtime",
        "",
        f"{'Importable':<22}: {'yes' if getattr(status, 'importable', False) else 'no'}",
        f"{'Distribution':<22}: {distribution}",
        f"{'Distribution state':<22}: {state}",
    ]
    if module_version:
        lines.append(f"{'Module version':<22}: {module_version}")
    module_path = getattr(status, "module_path", None)
    if module_path is not None:
        lines.append(f"{'Module path':<22}: {module_path}")
    build_device = getattr(status, "build_device", None)
    if build_device:
        lines.append(f"{'Build device':<22}: {build_device}")
    available = tuple(getattr(status, "available_providers", ()))
    configured = tuple(getattr(status, "configured_providers", ()))
    resolved = tuple(getattr(status, "resolved_providers", ()))
    lines.extend(["", "Available providers:"])
    lines.extend(f"  {provider}" for provider in available)
    if not available:
        lines.append("  (none reported)")
    lines.extend(
        [
            f"{'Configured policy':<22}: {_provider_names(configured, empty='none')}",
            f"{'Policy source':<22}: {_provider_source_label(getattr(status, 'provider_source', 'unknown'))}",
            "Resolved for new sessions:",
        ]
    )
    if resolved:
        lines.extend(f"  {provider}" for provider in resolved)
    else:
        lines.append("  (none reported)")
    if state == "ambiguous":
        lines.extend(
            [
                "",
                "Warning: multiple ONNX Runtime distributions claim the imported module.",
                "Keep one ONNX Runtime distribution per Python environment.",
            ]
        )
    import_error = getattr(status, "import_error", None)
    if import_error:
        lines.extend(["", f"Import error: {import_error}"])
    resolution_error = getattr(status, "resolution_error", None)
    if resolution_error:
        lines.extend(["", f"Provider resolution error: {resolution_error}"])
    return "\n".join(lines)


def _format_runtime_list(status: Any) -> str:
    lines = [
        "Installed ONNX Runtime distributions",
        "NAME                 VERSION       IMPORT OWNER",
    ]
    distributions = getattr(status, "distributions", ())
    if distributions:
        for distribution in distributions:
            owner = "yes" if getattr(distribution, "owns_import", False) else "no"
            lines.append(
                f"{getattr(distribution, 'name', 'unknown'):<20} "
                f"{getattr(distribution, 'version', 'unknown'):<13} {owner}"
            )
    else:
        lines.append("(none detected)")
    resolved = set(getattr(status, "resolved_providers", ()))
    lines.extend(
        ["", "Providers reported by imported ORT", "PROVIDER                     SELECTED"]
    )
    providers = getattr(status, "available_providers", ())
    if providers:
        lines.extend(
            f"{provider:<29} {'yes' if provider in resolved else 'no'}" for provider in providers
        )
    else:
        lines.append("(none reported)")
    return "\n".join(lines)


def _format_runtime_use(providers: str, status: Any) -> str:
    lines = [
        f"Saved ONNX provider policy: {providers}",
        f"Effective source is currently: {_provider_source_label(getattr(status, 'provider_source', 'unknown'))}",
        f"Effective resolved provider: {_provider_names(getattr(status, 'resolved_providers', ()))}",
        "Applies to new sessions.",
    ]
    return "\n".join(lines)


def _format_runtime_reset(status: Any) -> str:
    return "\n".join(
        [
            "Removed saved ONNX provider preference.",
            f"Effective source: {_provider_source_label(getattr(status, 'provider_source', 'unknown'))}",
            f"Resolved for new sessions: {_provider_names(getattr(status, 'resolved_providers', ()))}",
        ]
    )


def run_control_command(args: argparse.Namespace, api: Any) -> int:
    command = args.command
    if command == "runtime":
        if args.runtime_command == "use":
            api.set_runtime_providers(args.providers)
            result = api.runtime_status()
            if args.json:
                return _emit(_runtime_json_result(result), as_json=True)
            print(_format_runtime_use(args.providers, result))
            return 0
        if args.runtime_command == "reset":
            result = api.reset_runtime_providers()
            if args.json:
                return _emit(_runtime_json_result(result), as_json=True)
            print(_format_runtime_reset(result))
            return 0
        result = api.runtime_status()
        if args.json:
            return _emit(_runtime_json_result(result), as_json=True)
        formatter = (
            _format_runtime_status if args.runtime_command == "status" else _format_runtime_list
        )
        print(formatter(result))
        return 0

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
        if args.profile_command == "document":
            if args.profile_document_command == "inspect":
                requirements = api.inspect_document_voice_requirements(args.source)
                result = _document_voice_result(requirements)
                if args.json:
                    return _emit(result, as_json=True)
                print(_format_document_voice_requirements(requirements))
                return 0
            aliases = _role_assignments(args.same_as, "--same-as")
            requirements = api.inspect_document_voice_requirements(args.source)
            profile = api.create_document_profile(
                args.source,
                engine=args.engine,
                target=args.target,
                language=args.language,
                voices=_role_assignments(args.voice, "--voice"),
                speakers=_role_assignments(args.speaker, "--speaker"),
                aliases=aliases,
                options=_role_options(args.option),
                default_role=args.default_role,
                offline=args.offline,
                refresh=args.refresh,
                output=args.output,
                force=args.force,
            )
            result = {
                "source": requirements.source,
                "output": args.output,
                "discovered_roles": [
                    {"role": role.name, "blocks": role.blocks} for role in requirements.roles
                ],
                "bound_roles": list(profile.roles),
                "aliases": aliases,
                "default_role": profile.default_role,
                "profile_id": getattr(profile, "profile_id", None),
            }
            if args.json:
                return _emit(result, as_json=True)
            print(_format_document_profile_created(result))
            return 0
        elif args.profile_command == "create":
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
