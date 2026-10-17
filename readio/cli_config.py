"""Configuration commands for explicitly selected user settings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .config import init_settings, load_settings


class ConfigurationError(ValueError):
    """A malformed, unreadable, or conflicting user settings file."""


def add_config_commands(commands: Any) -> None:
    config = commands.add_parser("config", help="Create and inspect user settings")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    init = config_commands.add_parser("init", help="Create a settings template")
    init.add_argument("-o", "--output", type=Path, default=Path("readio.toml"))
    init.add_argument("--force", action="store_true")
    init.add_argument("--json", action="store_true")
    show = config_commands.add_parser("show", help="Show a settings file")
    show.add_argument("config", type=Path)
    show.add_argument("--json", action="store_true")
    validate = config_commands.add_parser("validate", help="Validate a settings file")
    validate.add_argument("config", type=Path)
    validate.add_argument("--json", action="store_true")


def run_config_command(args: argparse.Namespace) -> int:
    try:
        if args.config_command == "init":
            path = init_settings(args.output, force=args.force)
            result = {"path": str(path)}
            message = f"Created Readio settings: {path}"
        else:
            path = args.config
            settings = load_settings(path)
            if args.config_command == "show":
                result = {"path": str(path), "settings": settings}
                message = "\n".join(f"{key}: {value}" for key, value in settings.items())
            else:
                result = {"path": str(path), "valid": True}
                message = f"Valid Readio settings: {path}"
    except (OSError, TypeError, ValueError) as error:
        raise ConfigurationError(str(error)) from error

    if args.json:
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
    else:
        print(message)
    return 0


__all__ = ["ConfigurationError", "add_config_commands", "run_config_command"]
